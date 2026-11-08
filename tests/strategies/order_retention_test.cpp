#include "test_support.hpp"

#include "fastmm/strategies/basic_mm.hpp"
#include "fastmm/testing/strategy_harness.hpp"

#include <deque>

using namespace fastmm;
using fastmm::sim::StrategyHarness;

namespace {
TradeMsg trade(std::uint64_t id, std::int64_t quantity, Side side) {
  TradeMsg t{};
  init_header(t, EventType::Trade, InstrumentId{0}, VenueId{0});
  t.trade_id = id;
  t.qty = Qty::from_raw(quantity);
  t.price = 75000_px;
  t.aggressor = side;
  return t;
}
ParamMap parameters(int mode) {
  return {{"gate_mode", std::to_string(mode)},
          {"gate_window_ms", "100"},
          {"gate_trade_num", "1"},
          {"gate_trade_den", "2"},
          {"gate_book_num", "1"},
          {"gate_book_den", "2"},
          {"quote_qty", "0.002"},
          {"max_inventory", "0.006"},
          {"requote_threshold_ticks", "10"},
          {"inventory_soft_ms", "15000"},
          {"inventory_hard_ms", "60000"}};
}
void top(StrategyHarness<BasicMM>& h, std::uint64_t seq, Qty bid, Qty ask) {
  BookTickerMsg t{};
  init_header(t, EventType::BookTicker, h.instrument(), VenueId{0});
  t.hdr.venue_seq = seq;
  t.bid_px = 75000_px;
  t.ask_px = *Price::from_decimal("75000.1");
  t.bid_qty = bid;
  t.ask_qty = ask;
  h.push(t.hdr);
}
void adverse(StrategyHarness<BasicMM>& h) {
  top(h, 100, 1_qty, 10_qty);
  h.advance(milliseconds(101));
  auto t = trade(1, 100000000, Side::Sell);
  h.push(t.hdr);
  h.advance(milliseconds(1));
}
}  // namespace

TEST_CASE("retention.flow: exact boundaries and prefix sums agree with an independent event sum") {
  FlowWindow<4096> flow;
  Timestamp now{seconds(1700000000).ns};
  flow.reset(now, FlowWindow<4096>::ResetReason::Startup);
  struct Item {
    Timestamp at;
    std::int64_t qty;
    Side side;
  };
  std::deque<Item> oracle;
  for (std::uint64_t i = 1; i <= 3000; ++i) {
    now = now + milliseconds(13 + static_cast<std::int64_t>(i % 47));
    const auto t = trade(i, static_cast<std::int64_t>(i % 31 + 1), i % 3 ? Side::Sell : Side::Buy);
    flow.trade(now, t);
    oracle.push_back({now, t.qty.raw, t.aggressor});
    while (oracle.front().at <= now - seconds(60)) oracle.pop_front();
    for (const auto ms : kRetentionWindowsMs) {
      const auto f = flow.totals(now, milliseconds(ms));
      if (!f.valid) continue;
      std::int64_t buy = 0, sell = 0;
      for (const auto& e : oracle)
        if (e.at > now - milliseconds(ms)) (e.side == Side::Buy ? buy : sell) += e.qty;
      CHECK(f.buy == buy);
      CHECK(f.sell == sell);
    }
  }
  const auto t = trade(3000, 1000, Side::Buy);
  flow.trade(now, t);
  CHECK(flow.duplicates() == 1);
  flow.advance(now + seconds(60));
  CHECK(flow.totals(now + seconds(60), seconds(60)).buy == 0);
  CHECK(flow.totals(now + seconds(60), seconds(60)).sell == 0);
}

TEST_CASE("retention.flow: missing trades overflow and clock reversal invalidate warmup") {
  FlowWindow<2> f;
  Timestamp now{seconds(1700000000).ns};
  f.reset(now, FlowWindow<2>::ResetReason::Startup);
  f.trade(now, trade(1, 1, Side::Buy));
  f.trade(now + milliseconds(100), trade(3, 1, Side::Buy));
  CHECK_FALSE(f.totals(now + milliseconds(100), milliseconds(100)).valid);
  CHECK(f.reason() == FlowWindow<2>::ResetReason::TradeGap);
  f.trade(now + milliseconds(101), trade(4, 1, Side::Buy));
  f.trade(now + milliseconds(102), trade(5, 1, Side::Buy));
  CHECK(f.reason() == FlowWindow<2>::ResetReason::CapacityExceeded);
  f.advance(now);
  CHECK(f.reason() == FlowWindow<2>::ResetReason::Clock);
  CHECK_FALSE(f.totals(now, milliseconds(100)).valid);
}

TEST_CASE("retention.gate: exact rational boundary mirrored sides and neutral flow") {
  const RetentionThreshold threshold{1, 3};
  CHECK(OrderRetentionGate::evaluate({1, 2, true}, 1_qty, 2_qty, threshold, threshold) == 1);
  CHECK(OrderRetentionGate::evaluate({2, 1, true}, 2_qty, 1_qty, threshold, threshold) == 2);
  CHECK(OrderRetentionGate::evaluate({0, 0, true}, 1_qty, 2_qty, {0, 1}, {0, 1}) == 0);
  CHECK(OrderRetentionGate::evaluate({1, 2, false}, 1_qty, 2_qty, threshold, threshold) == 0);
  CHECK_FALSE(RetentionThreshold{2, 3}.accepts(1, 3));
}

TEST_CASE("retention.gate: wait for aggregate leaves before a no-cross-zero reduction") {
  OrderRetentionGate gate;
  DesiredQuotes q;
  q.bid(100_px, 2_qty);
  q.bid(99_px, 2_qty);
  gate.constrain(q, -1_qty, 4_qty, 0_qty, OrderRetentionGate::kBuy);
  CHECK(q.bids.empty());
  q.bid(100_px, 2_qty);
  q.bid(99_px, 2_qty);
  gate.constrain(q, -1_qty, 0_qty, 0_qty, OrderRetentionGate::kBuy);
  CHECK(q.bids[0].qty == 1_qty);
  CHECK(q.bids[1].qty == 0_qty);
}

TEST_CASE(
    "retention.strategy: active cancels adverse side without a price move and timer restores") {
  StrategyHarness<BasicMM> h(parameters(2));
  adverse(h);
  REQUIRE(h.working_orders().size() == 1);
  CHECK(h.working_orders()[0].side == Side::Sell);
  h.advance(milliseconds(200));
  CHECK(h.working_orders().size() == 2);
}

TEST_CASE("retention.strategy: observe retains the baseline orders") {
  StrategyHarness<BasicMM> h(parameters(1));
  adverse(h);
  CHECK(h.working_orders().size() == 2);
}

TEST_CASE("retention.strategy: an unchanged gate does not accelerate inventory exit on trades") {
  auto p = parameters(2);
  p["gate_trade_den"] = "1";
  p["gate_book_den"] = "1";  // With positive quantities, |imbalance| cannot equal one.
  p["inventory_soft_ms"] = "150";
  p["inventory_hard_ms"] = "1000";
  StrategyHarness<BasicMM> h(p);
  h.book("75000", "75000.10");
  h.advance(milliseconds(1));
  REQUIRE(h.fill(Side::Buy, 0.001_qty));
  h.advance(milliseconds(151));
  REQUIRE(h.working_orders().size() == 2);
  auto t = trade(1, 100000000, Side::Sell);
  h.push(t.hdr);
  h.advance(milliseconds(1));
  CHECK(h.working_orders().size() == 2);
  h.advance(milliseconds(100));
  REQUIRE(h.working_orders().size() == 1);
  CHECK(h.working_orders()[0].side == Side::Sell);
}

TEST_CASE("retention.strategy: active never receives implicit fitted parameters") {
  BasicMM s;
  CHECK(s.configure({{"gate_mode", "2"}}).has_value());
  CHECK_FALSE(s.configure(parameters(2)).has_value());
}

TEST_CASE("retention.strategy: warmup and drain do not disable hard exit") {
  const auto start = seconds(1700000000).ns;
  auto p = parameters(0);
  p["entry_start_ns"] = std::to_string(start + milliseconds(200).ns);
  p["entry_stop_ns"] = std::to_string(start + milliseconds(500).ns);
  p["pull_on_stale_ms"] = "0";
  StrategyHarness<BasicMM> h(p);
  h.book("75000", "75000.10");
  h.advance(milliseconds(100));
  CHECK(h.working_orders().empty());
  h.advance(milliseconds(200));
  REQUIRE(h.working_orders().size() == 2);
  REQUIRE(h.fill(Side::Buy, 0.001_qty));
  h.advance(milliseconds(301));
  REQUIRE(h.working_orders().size() == 1);
  CHECK(h.working_orders()[0].side == Side::Sell);
  CHECK(h.working_orders()[0].leaves_qty() <= 0.001_qty);
  const auto sent = h.engine().stats().orders_sent;
  h.advance(seconds(61));
  CHECK(h.engine().stats().orders_sent > sent);
}
