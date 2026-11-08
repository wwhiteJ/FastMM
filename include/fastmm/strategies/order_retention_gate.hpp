#pragma once

#include "fastmm/core/quote_manager.hpp"
#include "fastmm/strategies/flow_window.hpp"

#include <limits>

namespace fastmm {

struct RetentionThreshold {
  std::int64_t numerator;
  std::int64_t denominator;
  [[nodiscard]] bool accepts(std::int64_t adverse, std::int64_t total) const noexcept {
    return adverse > 0 && total > 0 && numerator >= 0 && denominator > 0 &&
           static_cast<Int128>(adverse) * denominator >= static_cast<Int128>(total) * numerator;
  }
};

class OrderRetentionGate {
 public:
  static constexpr unsigned kBuy = 1, kSell = 2;

  [[nodiscard]] static unsigned evaluate(FlowTotals flow,
                                         Qty bid,
                                         Qty ask,
                                         RetentionThreshold trade,
                                         RetentionThreshold book) noexcept {
    return trade_side(flow, trade) & book_side(bid, ask, book);
  }

  [[nodiscard]] static unsigned trade_side(FlowTotals flow, RetentionThreshold trade) noexcept {
    if (!flow.valid) return 0;
    const auto volume = flow.buy + flow.sell;
    if (trade.accepts(flow.sell - flow.buy, volume)) return kBuy;
    if (trade.accepts(flow.buy - flow.sell, volume)) return kSell;
    return 0;
  }
  [[nodiscard]] static unsigned book_side(Qty bid, Qty ask, RetentionThreshold book) noexcept {
    if (!bid.is_positive() || !ask.is_positive() ||
        bid.raw > std::numeric_limits<std::int64_t>::max() - ask.raw)
      return 0;
    const auto depth = bid.raw + ask.raw;
    if (book.accepts(ask.raw - bid.raw, depth)) return kBuy;
    if (book.accepts(bid.raw - ask.raw, depth)) return kSell;
    return 0;
  }

  // Clear the old side before shrinking its aggregate leaves. QuoteManager's ordinary quantity
  // hysteresis intentionally keeps larger orders, which is unsafe for a no-cross-zero cap.
  void constrain(
      DesiredQuotes& quotes, Qty position, Qty open_buy, Qty open_sell, unsigned mask) noexcept {
    constrain_side(quotes.bids,
                   position.is_negative() ? position.abs() : Qty{},
                   open_buy,
                   (mask & kBuy) != 0,
                   draining_buy_);
    constrain_side(quotes.asks,
                   position.is_positive() ? position : Qty{},
                   open_sell,
                   (mask & kSell) != 0,
                   draining_sell_);
  }

 private:
  static void constrain_side(StaticVector<Level, kMaxQuoteLevels>& levels,
                             Qty budget,
                             Qty open,
                             bool blocked,
                             bool& draining) noexcept {
    if (!blocked) {
      draining = false;
      return;
    }
    if (open > budget) draining = true;
    if (draining && open.is_positive()) {
      levels.clear();
      return;
    }
    draining = false;
    for (auto& level : levels) {
      level.qty = std::min(level.qty, budget);
      budget -= level.qty;
    }
  }
  bool draining_buy_ = false, draining_sell_ = false;
};

}  // namespace fastmm
