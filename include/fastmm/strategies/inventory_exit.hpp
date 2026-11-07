#pragma once

#include "fastmm/core/oms.hpp"
#include "fastmm/core/order.hpp"
#include "fastmm/core/quote_manager.hpp"

#include <algorithm>

namespace fastmm {

// One inventory episode lasts from flat to flat. Partial reductions do not reset its age.
class InventoryExit {
 public:
  enum class Mode { Normal, Reduce, Hard };

  template <class Params>
  bool update(Qty position, Timestamp now, const Params& p) noexcept {
    const Mode before = mode_;
    if (position.is_zero()) {
      opened_ = {};
      mode_ = Mode::Normal;
    } else {
      if (!opened_.valid()) opened_ = now;
      if (p.inventory_soft_ms.ns > 0 && now - opened_ >= p.inventory_soft_ms)
        mode_ = std::max(mode_, Mode::Reduce);
      if (p.inventory_reduce_pct > 0 && p.max_inventory.is_positive() &&
          static_cast<Int128>(position.abs().raw) * 100 >=
              static_cast<Int128>(p.max_inventory.raw) * p.inventory_reduce_pct)
        mode_ = std::max(mode_, Mode::Reduce);
      if (p.inventory_hard_ms.ns > 0 && now - opened_ >= p.inventory_hard_ms) mode_ = Mode::Hard;
    }
    return before != mode_;
  }

  [[nodiscard]] Mode mode() const noexcept { return mode_; }
  [[nodiscard]] Duration age(Timestamp now) const noexcept {
    return opened_.valid() ? now - opened_ : Duration{};
  }
  void await_cancels() noexcept { cancelling_ = true; }

  template <class Ctx>
  bool cancelled(Ctx& ctx, InstrumentId id) noexcept {
    if (!cancelling_) return true;
    if (ctx.open_qty(id, Side::Buy).is_positive() || ctx.open_qty(id, Side::Sell).is_positive())
      return false;
    cancelling_ = false;
    return true;
  }

  // Total exit quantity across levels cannot reverse the current position.
  void constrain(DesiredQuotes& q, Qty position) const noexcept {
    if (mode_ == Mode::Normal) return;
    const bool long_position = position.is_positive();
    (long_position ? q.bids : q.asks).clear();
    auto& exits = long_position ? q.asks : q.bids;
    Qty remaining = position.abs();
    for (auto& level : exits) {
      level.qty = std::min(level.qty, remaining);
      remaining -= level.qty;
    }
  }

  template <class Ctx, class Params, class Top>
  bool liquidate(Ctx& ctx, const Instrument& inst, const Params& p, const Top& top) noexcept {
    const Timestamp now = ctx.now();
    if (now < next_attempt_ || !ctx.quoting_enabled() || ctx.reconciling()) return false;
    if (ctx.open_qty(inst.id, Side::Buy).is_positive() ||
        ctx.open_qty(inst.id, Side::Sell).is_positive())
      return false;
    const Qty position = ctx.position(inst.id).qty;
    const Qty qty = inst.round_qty(std::min(position.abs(), p.quote_qty));
    if (!qty.is_positive()) return false;
    const Side side = position.is_positive() ? Side::Sell : Side::Buy;
    const Price touch = side == Side::Buy ? top.ask() : top.bid();
    const Price limit = side == Side::Buy ? touch + touch * p.exit_slippage_bps
                                          : touch - touch * p.exit_slippage_bps;
    next_attempt_ = now + p.exit_retry_ms;
    const auto result =
        ctx.send(NewOrderRequest::limit(inst.id, side, inst.round_price(limit, side), qty)
                     .ioc()
                     .reduce_only());
    last_reject_ = result ? RejectReason::None : result.error();
    if (result) last_ioc_ = result.value();
    return true;
  }
  bool observe(const OmsUpdate& u) noexcept {
    if (!last_ioc_.valid() || u.order.cl_ord_id != last_ioc_) return false;
    if (u.refused != RejectReason::None) last_reject_ = u.refused;
    return u.terminal || u.refused != RejectReason::None;
  }
  [[nodiscard]] RejectReason last_reject() const noexcept { return last_reject_; }

 private:
  Mode mode_ = Mode::Normal;
  Timestamp opened_{};
  Timestamp next_attempt_{};
  ClientOrderId last_ioc_{};
  bool cancelling_ = false;
  RejectReason last_reject_ = RejectReason::None;
};

}  // namespace fastmm
