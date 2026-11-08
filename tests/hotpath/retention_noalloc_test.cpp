#include "alloc_counter.hpp"
#include "test_support.hpp"

#include "fastmm/strategies/order_retention_gate.hpp"

using namespace fastmm;

TEST_CASE("hotpath.noalloc: exact flow window and retention sizing") {
  auto flow = std::make_unique<FlowWindow<4096>>();
  Timestamp now{seconds(1700000000).ns};
  flow->reset(now, FlowWindow<4096>::ResetReason::Startup);
  OrderRetentionGate gate;
  std::uint64_t accumulated = 0;
  {
    test::NoAllocScope scope;
    for (std::uint64_t n = 1; n < 2000; ++n) {
      now = now + milliseconds(1);
      TradeMsg t{};
      t.trade_id = n;
      t.qty = 1_qty;
      t.aggressor = Side::Sell;
      flow->trade(now, t);
      const auto mask = OrderRetentionGate::evaluate(
          flow->totals(now, milliseconds(100)), 1_qty, 9_qty, {1, 2}, {1, 2});
      DesiredQuotes quotes;
      quotes.bid(100_px, 1_qty);
      gate.constrain(quotes, 0_qty, 1_qty, 0_qty, mask);
      accumulated += mask;
    }
  }
  CHECK(accumulated > 0);
}
