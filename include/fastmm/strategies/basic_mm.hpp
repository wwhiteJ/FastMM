#pragma once
// BasicMM (8.6): symmetric quotes around mid, half_spread_bps wide, skewed by inventory,
// with a per-side inventory cap. Each side is cut to what the account's balance on the venue
// covers (fit_to_balance): a spot sell to the base held, a buy to the quote, and a side the balance
// cannot cover at all is not quoted. Integer math throughout: bps parameters are Ratios (0.0001 bp
// resolution) and quantities are parsed exactly.
#include "fastmm/core/book/book_view.hpp"
#include "fastmm/core/fixed_point.hpp"
#include "fastmm/core/instrument.hpp"
#include "fastmm/core/messages.hpp"
#include "fastmm/core/oms.hpp"
#include "fastmm/core/quote_decision.hpp"
#include "fastmm/core/quote_manager.hpp"
#include "fastmm/core/time.hpp"
#include "fastmm/strategies/inventory_exit.hpp"
#include "fastmm/strategies/latest_bbo.hpp"
#include "fastmm/strategies/quoting.hpp"
#include "fastmm/strategies/retention_state.hpp"
#include "fastmm/strategies/strategy.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace fastmm {

struct BasicMMParams {
  FASTMM_PARAMS(BasicMMParams)
  FASTMM_PARAM_BPS(half_spread_bps, 5_bps, 0_bps, 10000_bps, "half spread around mid, basis points")
  FASTMM_PARAM_BPS(skew_bps_per_unit,
                   1_bps,
                   0_bps,
                   10000_bps,
                   "shift both quotes by this many bps per quote_qty of inventory")
  FASTMM_PARAM(Qty, quote_qty, 0.01_qty, 0_qty, 1000000000_qty, "quantity per level (base units)")
  FASTMM_PARAM(Qty,
               max_inventory,
               0.1_qty,
               0_qty,
               1000000000_qty,
               "stop quoting the side that would grow |position| past this (0 = no cap)")
  FASTMM_PARAM(
      int, requote_threshold_ticks, 1, 0, 1000000, "ignore mid moves smaller than this many ticks")
  FASTMM_PARAM_MS(pull_on_stale_ms,
                  milliseconds(2000),
                  milliseconds(0),
                  milliseconds(3600000),
                  "pull quotes when the book is older than this (0 = never)")
  FASTMM_PARAM(bool, trace_quotes, false, false, true, "emit quote decisions through the async log")
  FASTMM_PARAM(
      int, inventory_reduce_pct, 0, 0, 100, "latch reduce-only at this percent of cap (0 = off)")
  FASTMM_PARAM_MS(inventory_soft_ms,
                  milliseconds(0),
                  milliseconds(0),
                  milliseconds(3600000),
                  "reduce-only after this flat-to-flat inventory age (0 = off)")
  FASTMM_PARAM_MS(inventory_hard_ms,
                  milliseconds(0),
                  milliseconds(0),
                  milliseconds(3600000),
                  "cancel then reduce-only IOC after this age (0 = off)")
  FASTMM_PARAM_BPS(exit_slippage_bps, 5_bps, 0_bps, 100_bps, "IOC limit beyond the current touch")
  FASTMM_PARAM_MS(exit_retry_ms,
                  milliseconds(1000),
                  milliseconds(100),
                  milliseconds(60000),
                  "minimum interval between hard-exit attempts")
  FASTMM_PARAM(int, levels, 1, 1, 8, "quote levels per side")
  FASTMM_PARAM(int, level_step_ticks, 1, 1, 100000, "tick distance between successive levels")
  FASTMM_PARAM(int,
               gate_mode,
               0,
               0,
               2,
               "retention: 0 off, 1 observe, 2 research/validated active; restart to enable")
  FASTMM_PARAM_MS(gate_window_ms,
                  milliseconds(0),
                  milliseconds(0),
                  milliseconds(60000),
                  "explicit calibrated flow window; 0 is unset")
  FASTMM_PARAM(
      std::int64_t, gate_trade_num, -1, -1, INT64_MAX, "flow threshold numerator; -1 is unset")
  FASTMM_PARAM(
      std::int64_t, gate_trade_den, 0, 0, INT64_MAX, "flow threshold denominator; 0 is unset")
  FASTMM_PARAM(
      std::int64_t, gate_book_num, -1, -1, INT64_MAX, "book threshold numerator; -1 is unset")
  FASTMM_PARAM(
      std::int64_t, gate_book_den, 0, 0, INT64_MAX, "book threshold denominator; 0 is unset")
  FASTMM_PARAM(std::int64_t,
               entry_start_ns,
               0,
               0,
               INT64_MAX,
               "research warmup: no entry before this engine timestamp")
  FASTMM_PARAM(std::int64_t,
               entry_stop_ns,
               0,
               0,
               INT64_MAX,
               "research drain: no risk increase at or after this timestamp")
  FASTMM_PARAM(
      int, gate_rule, 0, 0, 3, "research control: 0 joint, 1 flow, 2 book, 3 scheduled mask")
  FASTMM_PARAM(int, gate_forced_mask, 0, 0, 2, "scheduled research control: 0 clear, 1 buy, 2 sell")
  FASTMM_PARAM(std::int64_t,
               gate_trace_start_ns,
               0,
               0,
               INT64_MAX,
               "diagnostic output begins at this timestamp")
  [[nodiscard]] std::optional<std::string> validate() const {
    if (inventory_hard_ms.ns > 0 && inventory_soft_ms >= inventory_hard_ms)
      return "inventory_hard_ms must exceed inventory_soft_ms";
    if ((inventory_soft_ms.ns > 0 || inventory_hard_ms.ns > 0 || inventory_reduce_pct > 0) &&
        !quote_qty.is_positive())
      return "inventory exit needs positive quote_qty";
    if (gate_mode == 2 && gate_rule != 3 &&
        (gate_window_ms.ns == 0 || gate_trade_num < 0 || gate_trade_den <= 0 || gate_book_num < 0 ||
         gate_book_den <= 0 || gate_trade_num > gate_trade_den || gate_book_num > gate_book_den))
      return "active retention requires an explicit window and exact thresholds";
    if (gate_rule == 3 && (gate_mode != 2 || !entry_start_ns || !entry_stop_ns))
      return "scheduled masks require an explicitly bounded research replay";
    if (entry_stop_ns && (entry_stop_ns <= entry_start_ns || inventory_hard_ms.ns <= 0))
      return "research drain requires ordered boundaries and enabled hard exit";
    return std::nullopt;
  }
};

class BasicMM : public StrategyBase<BasicMMParams, ParamScope::PerInstrument> {
 public:
  static constexpr std::string_view name() noexcept { return "basic_mm"; }
  static constexpr std::uint64_t kStaleTimer = 0x5741'4c45;  // "STALE"
  static constexpr std::uint64_t kAccountingTimer = 0x4143'4354;

  template <class Ctx>
  void on_start(Ctx& ctx) noexcept {
    for (auto& state : states_) state = State{};
    for (const auto& inst : ctx.instruments())
      states_[inst.id.value].retention.prepare(params(inst.id).gate_mode, ctx.now());
    stale_timer_ = ctx.every(milliseconds(100), kStaleTimer);
    for (const auto& inst : ctx.instruments()) {
      const auto& p = params(inst.id);
      if (p.gate_mode || p.entry_start_ns || p.entry_stop_ns) {
        const auto hour = seconds(3600).ns;
        static_cast<void>(ctx.once(Duration{hour - ctx.now().ns % hour}, kAccountingTimer));
        break;
      }
    }
  }

  template <class Ctx, class Book>
  void on_book(Ctx& ctx, InstrumentId id, const Book& book) noexcept {
    states_[id.value].top.depth(book, ctx.now());
    decide(ctx, id);
  }

  template <class Ctx>
  void on_book_ticker(Ctx& ctx, InstrumentId id, const BookTickerMsg& m) noexcept {
    if (states_[id.value].top.ticker(m, ctx.now())) decide(ctx, id);
  }

  template <class Ctx>
  void on_trade(Ctx& ctx, InstrumentId id, const TradeMsg& m) noexcept {
    const auto& p = params(id);
    if (p.gate_mode == 0) return;
    auto& state = states_[id.value];
    state.retention.trade(ctx.now(), m);
    if (p.gate_mode != 2 || !state.retention.update(ctx.now(), state.top, p)) return;
    // Trade callbacks change quotes only when the gate changes. They must not accelerate the
    // baseline inventory lifecycle merely because feature collection adds an on_trade hook.
    state.last_mid = {};
    state.retention.trace(ctx, id, state.top, p);
    decide(ctx, id);
  }

  template <class Ctx>
  void on_fill(Ctx& ctx, const Fill& fill) noexcept {
    State& state = states_[fill.instrument.value];
    // A partial exit changes the maximum safe resting quantity: cancel before re-sizing it.
    if (state.exit.mode() != InventoryExit::Mode::Normal) pull(ctx, fill.instrument);
    state.last_mid = {};
    decide(ctx, fill.instrument);
  }

  template <class Ctx>
  void on_order_update(Ctx& ctx, const OmsUpdate& u) noexcept {
    if (!u.known || !u.changed) return;
    State& state = states_[u.order.instrument.value];
    state.retry = true;  // a deferred quote may have been refused while another cancel was pending
    if (state.exit.observe(u)) trace(ctx, u.order.instrument, "exit_order_update");
    if (state.pulled || state.exit.mode() != InventoryExit::Mode::Normal ||
        state.retention.mask() || state.entry_phase != 0)
      decide(ctx, u.order.instrument);
  }

  template <class Ctx>
  void on_timer(Ctx& ctx, TimerId, std::uint64_t tag) noexcept {
    if (tag == kAccountingTimer) {
      account(ctx);
      static_cast<void>(ctx.once(seconds(3600), kAccountingTimer));
      return;
    }
    if (tag != kStaleTimer) return;
    for (const Instrument& inst : ctx.instruments()) {
      State& state = states_[inst.id.value];
      if (state.retry) state.last_mid = {};
      decide(ctx, inst.id);
      state.retention.trace(ctx, inst.id, state.top, params(inst.id));
    }
  }

  template <class Ctx>
  void on_connection(Ctx& ctx, const ConnectionStateMsg& m) noexcept {
    for (const Instrument& inst : ctx.instruments()) {
      if (inst.venue != m.hdr.venue || (m.hdr.instrument.valid() && m.hdr.instrument != inst.id))
        continue;
      State& state = states_[inst.id.value];
      state.last_mid = {};
      if (m.channel == 0) {
        state.top = LatestBbo{};
        state.retention.reset(ctx.now());
      }
      if (m.channel == 1) state.order_live = m.state == ConnState::Live;
      if (m.state != ConnState::Live) {
        pull(ctx, inst.id);
        continue;
      }
      state.top.depth(ctx.book(inst.id), ctx.now());
      decide(ctx, inst.id);
    }
  }

  template <class Ctx>
  void on_balance(Ctx& ctx, const BalanceMsg& m) noexcept {
    for (const Instrument& inst : ctx.instruments()) {
      if (inst.venue != m.hdr.venue) continue;
      states_[inst.id.value].last_mid = {};
      decide(ctx, inst.id);
    }
  }

  template <class Ctx>
  void on_quoting(Ctx& ctx, bool enabled) noexcept {
    for (const Instrument& inst : ctx.instruments()) {
      states_[inst.id.value].last_mid = {};
      if (enabled) decide(ctx, inst.id);
    }
  }

  template <class Ctx>
  void on_params(Ctx& ctx) noexcept {
    for (const Instrument& inst : ctx.instruments()) {
      states_[inst.id.value].last_mid = {};
      decide(ctx, inst.id);
    }
  }

  // Pure quoting function; exposed for deterministic tests. Truncation matches the centi-bps
  // formula it replaced (mid.raw * cbps / 1e6 == mid.raw * (cbps * 100) / 1e8).
  [[nodiscard]] DesiredQuotes compute_quotes(Price mid,
                                             Qty position,
                                             const Instrument& inst) const noexcept {
    const BasicMMParams& p = params(inst.id);
    DesiredQuotes q;
    if (p.quote_qty.is_zero()) return q;
    const std::int64_t inventory_units = position / p.quote_qty;  // signed, truncating
    const Price half = mid * p.half_spread_bps;
    const Price centre = mid - mid * (p.skew_bps_per_unit * inventory_units);
    const Price step = inst.ticks(p.level_step_ticks);
    const Qty qty = inst.round_qty(p.quote_qty);
    const bool can_buy = inventory_allows(Side::Buy, position, p.quote_qty, p.max_inventory);
    const bool can_sell = inventory_allows(Side::Sell, position, p.quote_qty, p.max_inventory);
    for (int l = 0; l < p.levels; ++l) {
      if (can_buy) q.bid(inst.round_price(centre - half - step * l, Side::Buy), qty);
      if (can_sell) q.ask(inst.round_price(centre + half + step * l, Side::Sell), qty);
    }
    q.uncross(inst.tick);
    return q;
  }

 private:
  template <class Ctx>
  void account(Ctx& ctx) const noexcept {
    for (const auto& inst : ctx.instruments()) {
      const auto& p = ctx.position(inst.id);
      const auto& top = states_[inst.id.value].top;
      ctx.trace_retention_equity(
          RetentionEquity{ctx.now(),
                          inst.id,
                          p.qty,
                          p.avg_px,
                          top.bid(),
                          top.ask(),
                          p.realized,
                          p.fees,
                          top.fresh(ctx.now(), params(inst.id).pull_on_stale_ms)});
    }
  }

  struct State {
    LatestBbo top;
    InventoryExit exit;
    RetentionState retention;
    OrderRetentionGate boundary;
    Price last_mid{};
    Price desired_bid{};
    Price desired_ask{};
    bool pulled = false;
    bool retry = false;
    bool order_live = true;
    int entry_phase = 0;
  };

  template <class Ctx>
  void decide(Ctx& ctx, InstrumentId id) noexcept {
    State& s = states_[id.value];
    const BasicMMParams& p = params(id);
    const int phase = p.entry_start_ns && ctx.now().ns < p.entry_start_ns  ? -1
                      : p.entry_stop_ns && ctx.now().ns >= p.entry_stop_ns ? 1
                                                                           : 0;
    if (phase != s.entry_phase) s.last_mid = {};
    s.entry_phase = phase;
    if (s.retention.update(ctx.now(), s.top, p)) {
      s.last_mid = {};
      s.retention.trace(ctx, id, s.top, p);
    }
    // The engine owns reconciliation holds and control pulls. Never override those holds.
    if (ctx.reconciling() || !ctx.quoting_enabled() || !s.order_live) return;
    const Qty position = ctx.position(id).qty;
    if (s.exit.update(position, ctx.now(), p)) {
      pull(ctx, id);
      trace(ctx, id, "inventory_transition");
    }
    if (!s.top.fresh(ctx.now(), p.pull_on_stale_ms)) {
      if (!s.pulled) {
        pull(ctx, id);
        trace(ctx, id, "invalid_or_stale");
      }
      return;
    }
    if (!s.exit.cancelled(ctx, id)) return;
    if (s.exit.mode() == InventoryExit::Mode::Hard) {
      if (s.exit.liquidate(ctx, ctx.instrument(id), p, s.top)) trace(ctx, id, "hard_exit");
      return;
    }
    const Instrument& inst = ctx.instrument(id);
    if (s.last_mid.is_positive() &&
        (s.top.mid() - s.last_mid).abs() < inst.ticks(p.requote_threshold_ticks))
      return;
    requote(ctx, id, inst);
  }

  template <class Ctx>
  void requote(Ctx& ctx, InstrumentId id, const Instrument& inst) noexcept {
    State& s = states_[id.value];
    DesiredQuotes q = compute_quotes(s.top.mid(), ctx.position(id).qty, inst);
    s.exit.constrain(q, ctx.position(id).qty);
    s.retention.constrain(
        q, ctx.position(id).qty, ctx.open_qty(id, Side::Buy), ctx.open_qty(id, Side::Sell));
    if (params(id).gate_mode == 2 && !s.retention.ready()) q.clear();
    if (s.entry_phase < 0) q.clear();
    if (s.entry_phase > 0)
      s.boundary.constrain(q,
                           ctx.position(id).qty,
                           ctx.open_qty(id, Side::Buy),
                           ctx.open_qty(id, Side::Sell),
                           OrderRetentionGate::kBuy | OrderRetentionGate::kSell);
    keep_passive(q, s.top.bid(), s.top.ask(), inst.tick);
    fit_to_balance(ctx, id, inst, q);
    const QuoteStats before = ctx.quote_stats();
    s.desired_bid = q.bids.empty() ? Price{} : q.bids[0].price;
    s.desired_ask = q.asks.empty() ? Price{} : q.asks[0].price;
    const bool accepted = ctx.set_quotes(id, q);
    const QuoteStats& after = ctx.quote_stats();
    s.retry = after.kept_interval != before.kept_interval || after.rejected != before.rejected ||
              after.kept_backoff != before.kept_backoff ||
              after.kept_rate_limit != before.kept_rate_limit ||
              after.kept_room != before.kept_room;
    s.last_mid = accepted ? s.top.mid() : Price{};
    s.pulled = !accepted;
    trace(ctx, id, accepted ? quote_reason(before, after) : "quoting_disabled");
    // Do not quiet depth callbacks: they also maintain BBO freshness during a quiet market.
  }

  [[nodiscard]] static std::string_view quote_reason(const QuoteStats& before,
                                                     const QuoteStats& after) noexcept {
    if (after.kept_interval != before.kept_interval) return "requote_interval";
    if (after.rejected != before.rejected) return "order_refused";
    if (after.kept_backoff != before.kept_backoff) return "reject_backoff";
    if (after.kept_rate_limit != before.kept_rate_limit) return "order_rate_limit";
    if (after.kept_room != before.kept_room) return "order_budget";
    if (after.skipped_pending != before.skipped_pending) return "pending_order";
    if (after.kept_hysteresis != before.kept_hysteresis) return "price_hysteresis";
    return "quote";
  }

  template <class Ctx>
  void pull(Ctx& ctx, InstrumentId id) noexcept {
    ctx.pull_quotes(id);
    State& s = states_[id.value];
    s.exit.await_cancels();
    s.last_mid = {};
    s.desired_bid = {};
    s.desired_ask = {};
    s.pulled = true;
  }

  template <class Ctx>
  void trace(Ctx& ctx, InstrumentId id, std::string_view reason) noexcept {
    const State& s = states_[id.value];
    const bool reducing = s.exit.mode() != InventoryExit::Mode::Normal;
    ctx.note_quote(id, Side::Buy, reducing ? "inventory_exit" : reason);
    ctx.note_quote(id, Side::Sell, reducing ? "inventory_exit" : reason);
    if (!params(id).trace_quotes) return;
    ctx.trace_quote(QuoteDecision{ctx.now(),
                                  id,
                                  s.top.sequence(),
                                  s.top.bid(),
                                  s.top.ask(),
                                  s.desired_bid,
                                  s.desired_ask,
                                  ctx.now() - s.top.received(),
                                  ctx.position(id).qty,
                                  s.exit.age(ctx.now()),
                                  static_cast<int>(s.exit.mode()),
                                  s.top.from_ticker(),
                                  s.exit.last_reject(),
                                  reason});
  }

  TimerId stale_timer_{};
  State states_[kMaxInstruments] = {};
};

static_assert(StrategyLike<BasicMM>);
static_assert(verify_strategy<BasicMM>());

}  // namespace fastmm
