#pragma once

#include "fastmm/core/quote_decision.hpp"
#include "fastmm/strategies/latest_bbo.hpp"
#include "fastmm/strategies/order_retention_gate.hpp"

#include <memory>
#include <new>

namespace fastmm {

class RetentionState {
 public:
  // A storage limit, not a signal parameter. Overflow invalidates the entire warmup window.
  static constexpr std::size_t kCapacity = 131072;
  using Window = FlowWindow<kCapacity>;

  void prepare(int mode, Timestamp now) noexcept {
    if (mode != 0) flow_.reset(new (std::nothrow) Window);
    if (flow_) flow_->reset(now, Window::ResetReason::Startup);
  }
  void reset(Timestamp now) noexcept {
    if (flow_) flow_->reset(now, Window::ResetReason::Connection);
    mask_ = 0;
  }
  void connection(Timestamp now, ConnState state) noexcept {
    connection_ = state == ConnState::Live ? TradeConnection::Live : TradeConnection::Down;
    reset(now);
  }
  void trade(Timestamp now, const TradeMsg& m) noexcept {
    if (flow_ && connection_ != TradeConnection::Down) flow_->trade(now, m);
  }
  template <class Params>
  bool update(Timestamp now, const LatestBbo& top, const Params& p) noexcept {
    if (!flow_) return false;
    flow_->advance(now);
    unsigned mask = 0;
    if (p.gate_mode == 2 && top.fresh(now, p.pull_on_stale_ms)) {
      const bool needs_trades = p.gate_rule == 0 || p.gate_rule == 1;
      const bool unavailable =
          connection_ == TradeConnection::Down || !flow_->totals(now, p.gate_window_ms).valid;
      if (needs_trades && connection_ != TradeConnection::Unreported && unavailable)
        mask = OrderRetentionGate::kBuy | OrderRetentionGate::kSell;
      else
        mask = evaluate(now, top, p);
    }
    const bool changed = mask_ != mask;
    mask_ = mask;
    return changed;
  }
  [[nodiscard]] unsigned mask() const noexcept { return mask_; }
  [[nodiscard]] bool ready() const noexcept { return flow_ != nullptr; }
  void constrain(DesiredQuotes& q, Qty position, Qty buy, Qty sell) noexcept {
    gate_.constrain(q, position, buy, sell, mask_);
  }
  template <class Ctx, class Params>
  void trace(Ctx& ctx, InstrumentId id, const LatestBbo& top, const Params& p) const noexcept {
    if (!flow_ || p.gate_mode == 0 || ctx.now().ns < p.gate_trace_start_ns) return;
    for (const auto ms : kRetentionWindowsMs) {
      if (p.gate_mode == 2 && milliseconds(ms) != p.gate_window_ms) continue;
      const auto f = flow_->totals(ctx.now(), milliseconds(ms));
      ctx.trace_retention(RetentionObservation{ctx.now(),
                                               id,
                                               top.sequence(),
                                               ms,
                                               top.bid_qty().raw,
                                               top.ask_qty().raw,
                                               f.buy,
                                               f.sell,
                                               connection_ != TradeConnection::Down && f.valid &&
                                                   top.fresh(ctx.now(), p.pull_on_stale_ms),
                                               mask_,
                                               static_cast<int>(flow_->reason()),
                                               flow_->resets(),
                                               flow_->duplicates()});
    }
  }

 private:
  // Older journals have no trade-channel events; preserve their original warmup semantics.
  enum class TradeConnection { Unreported, Live, Down };
  template <class Params>
  unsigned evaluate(Timestamp now, const LatestBbo& top, const Params& p) const noexcept {
    if (p.gate_rule == 3) return static_cast<unsigned>(p.gate_forced_mask);
    const auto flow = flow_->totals(now, p.gate_window_ms);
    const RetentionThreshold trade{p.gate_trade_num, p.gate_trade_den};
    const RetentionThreshold book{p.gate_book_num, p.gate_book_den};
    if (p.gate_rule == 1) return OrderRetentionGate::trade_side(flow, trade);
    if (p.gate_rule == 2) return OrderRetentionGate::book_side(top.bid_qty(), top.ask_qty(), book);
    return OrderRetentionGate::evaluate(flow, top.bid_qty(), top.ask_qty(), trade, book);
  }

  std::unique_ptr<Window> flow_;
  OrderRetentionGate gate_;
  unsigned mask_ = 0;
  TradeConnection connection_ = TradeConnection::Unreported;
};

}  // namespace fastmm
