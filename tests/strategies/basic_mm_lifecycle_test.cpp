#include "test_support.hpp"

#include "fastmm/strategies/basic_mm.hpp"
#include "fastmm/testing/strategy_harness.hpp"

using namespace fastmm;
using fastmm::sim::StrategyHarness;

namespace {
Price px(const char* s) {
  return *Price::from_decimal(s);
}
Qty qty(const char* s) {
  return *Qty::from_decimal(s);
}
ParamMap params() {
  return {{"half_spread_bps", "3"},
          {"quote_qty", "0.002"},
          {"max_inventory", "0.006"},
          {"skew_bps_per_unit", "0.5"},
          {"requote_threshold_ticks", "10"},
          {"pull_on_stale_ms", "2000"},
          {"inventory_reduce_pct", "80"},
          {"inventory_soft_ms", "15000"},
          {"inventory_hard_ms", "60000"}};
}
void ticker(StrategyHarness<BasicMM>& h, std::uint64_t seq, const char* bid, const char* ask) {
  BookTickerMsg m{};
  init_header(m, EventType::BookTicker, h.instrument(), VenueId{0});
  m.hdr.venue_seq = seq;
  m.bid_px = px(bid);
  m.ask_px = px(ask);
  m.bid_qty = m.ask_qty = qty("1");
  h.push(m.hdr);
}
sim::HarnessOptions throttled() {
  sim::HarnessOptions o;
  o.engine.quotes.min_requote_interval = milliseconds(100);
  return o;
}
}  // namespace

TEST_CASE("strategies.basic_mm: ticker drives quotes and older depth cannot overwrite it") {
  StrategyHarness<BasicMM> h(params());
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  ticker(h, 100, "75020", "75020.10");
  h.advance(milliseconds(1));
  auto orders = h.working_orders();
  REQUIRE(orders.size() == 2);
  CHECK(orders[0].price > px("74990"));
  const Price bid = orders[0].price;
  h.book("74900", "74900.10");  // depth sequence 2, ticker sequence 100
  h.advance(milliseconds(1));
  CHECK(h.working_orders()[0].price == bid);
  ticker(h, 99, "74800", "74800.10");
  h.advance(milliseconds(1));
  CHECK(h.working_orders()[0].price == bid);
}

TEST_CASE("strategies.basic_mm: invalid newest ticker pulls inside the requote interval") {
  StrategyHarness<BasicMM> h(params(), throttled());
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  REQUIRE(h.working_orders().size() == 2);
  ticker(h, 100, "75100", "75000");
  h.advance(milliseconds(1));
  CHECK(h.working_orders().empty());
  // An older valid depth cannot resurrect the invalid newer BBO.
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  CHECK(h.working_orders().empty());
  ticker(h, 101, "75100", "75100.10");
  h.advance(milliseconds(1));
  CHECK(h.working_orders().size() == 2);
}

TEST_CASE("strategies.basic_mm: throttled move is retried even if no new price arrives") {
  StrategyHarness<BasicMM> h(params(), throttled());
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  const Price old = h.working_orders()[0].price;
  ticker(h, 100, "75020", "75020.10");
  h.advance(milliseconds(1));
  CHECK(h.working_orders()[0].price == old);
  CHECK(h.engine().quote_manager().stats().kept_interval > 0);
  h.advance(milliseconds(150));
  REQUIRE(h.working_orders().size() == 2);
  CHECK(h.working_orders()[0].price > old);
}

TEST_CASE("strategies.basic_mm: ticker freshness survives older depth and expires locally") {
  StrategyHarness<BasicMM> h(params());
  h.book("75000", "75000.10");
  h.advance(milliseconds(1900));
  ticker(h, 100, "75000", "75000.10");
  h.advance(milliseconds(1500));
  CHECK(h.working_orders().size() == 2);
  h.book("75000", "75000.10");
  h.advance(milliseconds(600));
  CHECK(h.working_orders().empty());
}

TEST_CASE("strategies.basic_mm: soft timeout cancels entry and caps exits to partial inventory") {
  ParamMap p = params();
  p["pull_on_stale_ms"] = "0";
  StrategyHarness<BasicMM> h(p, throttled());
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  REQUIRE(h.fill(Side::Buy, qty("0.001")));
  h.advance(milliseconds(15100));
  auto orders = h.working_orders();
  REQUIRE(orders.size() == 1);
  CHECK(orders[0].side == Side::Sell);
  CHECK(orders[0].leaves_qty() == qty("0.001"));
  REQUIRE(h.fill(Side::Sell));
  h.advance(milliseconds(2));
  CHECK(h.engine().position(h.instrument()).qty.is_zero());
  CHECK(h.working_orders().size() == 2);
}

TEST_CASE("strategies.basic_mm: inventory threshold latches until flat") {
  ParamMap p = params();
  p["pull_on_stale_ms"] = "0";
  StrategyHarness<BasicMM> h(p);
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  for (int n = 0; n < 3; ++n) {
    REQUIRE(h.fill(Side::Buy));
    h.advance(milliseconds(1));
  }
  REQUIRE(h.engine().position(h.instrument()).qty == qty("0.006"));
  REQUIRE(h.working_orders().size() == 1);
  REQUIRE(h.fill(Side::Sell));
  h.advance(milliseconds(1));
  REQUIRE(h.working_orders().size() == 1);
  CHECK(h.working_orders()[0].side == Side::Sell);
}

TEST_CASE("strategies.basic_mm: hard timeout cancels makers before a bounded reduce-only IOC") {
  ParamMap p = params();
  p["pull_on_stale_ms"] = "0";
  StrategyHarness<BasicMM> h(p);
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  REQUIRE(h.fill(Side::Buy));
  h.advance(milliseconds(59950));
  const auto sent_before = h.engine().stats().orders_sent;
  h.advance(milliseconds(150));
  CHECK(h.working_orders().empty());
  CHECK(h.engine().stats().orders_sent > sent_before);
  // No external liquidity in this harness: IOC expires, retry waits rather than flooding.
  const auto sent = h.engine().stats().orders_sent;
  h.advance(milliseconds(500));
  CHECK(h.engine().stats().orders_sent == sent);
  h.advance(milliseconds(600));
  CHECK(h.engine().stats().orders_sent > sent);
}

TEST_CASE("strategies.basic_mm: exit configuration rejects contradictory timeouts") {
  BasicMM strategy;
  CHECK(strategy.configure({{"inventory_soft_ms", "60000"}, {"inventory_hard_ms", "15000"}}));
}

TEST_CASE("strategies.basic_mm: validated depth snapshots may restart their sequence") {
  L2Book<256> book;
  LatestBbo top;
  const std::array<Level, 1> bids{{{px("100"), qty("1")}}};
  const std::array<Level, 1> asks{{{px("101"), qty("1")}}};
  book.apply_snapshot(bids, asks, 100, Timestamp{seconds(1).ns});
  CHECK(top.depth(book, Timestamp{seconds(1).ns}));
  book.apply_snapshot(bids, asks, 1, Timestamp{seconds(2).ns});
  CHECK(top.depth(book, Timestamp{seconds(2).ns}));
  CHECK(top.sequence() == 1);
  CHECK(top.received() == Timestamp{seconds(2).ns});
}

TEST_CASE("strategies.basic_mm: deferred quotes recover after a jump crosses the old opposite") {
  ParamMap p = params();
  p["pull_on_stale_ms"] = "0";
  StrategyHarness<BasicMM> h(p);
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  ticker(h, 100, "75100", "75100.10");
  h.advance(milliseconds(1500));
  REQUIRE(h.working_orders().size() == 2);
  CHECK(h.working_orders()[0].price > px("75070"));
}

TEST_CASE("strategies.basic_mm: hard IOC closes a below-minimum residual without flipping") {
  ParamMap p = params();
  p["pull_on_stale_ms"] = "0";
  auto options = throttled();
  Instrument inst = options.instruments.get(InstrumentId{0});
  inst.asset_class = AssetClass::Perpetual;
  inst.min_notional = Notional::from_int(100);
  options.instruments = InstrumentTable{};
  static_cast<void>(options.instruments.add(inst));
  options.engine.risk.reduce_only_min_notional_exempt = true;
  StrategyHarness<BasicMM> h(p, options);
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  REQUIRE(h.fill(Side::Buy, qty("0.001")));
  h.advance(milliseconds(2));
  sim::NewOrder liquidity{};
  liquidity.account = 2;
  liquidity.cl_ord_id = ClientOrderId{1};
  liquidity.instrument = h.instrument();
  liquidity.side = Side::Buy;
  liquidity.price = px("75000");
  liquidity.qty = qty("1");
  static_cast<void>(h.venue_transport().matching_engine().submit(liquidity, h.now()));
  h.advance(milliseconds(60200));
  CHECK(h.engine().position(h.instrument()).qty.is_zero());
  CHECK(h.working_orders().size() == 2);
}
