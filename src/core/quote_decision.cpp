#include "fastmm/core/quote_decision.hpp"

#include "fastmm/core/log.hpp"

#include <fmt/format.h>

namespace fastmm {
void log_retention_equity(const RetentionEquity& d) noexcept {
  FASTMM_LOG_INFO(
      "retention_equity ts={} inst={} pos={} avg={} bid={} ask={} realized={} fees={} valid={}",
      d.at.ns,
      d.instrument.value,
      d.position.raw,
      d.average.raw,
      d.bid.raw,
      d.ask.raw,
      d.realized.raw,
      d.fees.raw,
      d.valid_mark);
}

void log_retention_observation(const RetentionObservation& d) noexcept {
  FASTMM_LOG_INFO(
      "retention ts={} inst={} seq={} w_ms={} bid_q={} ask_q={} buy_v={} sell_v={} "
      "valid={} blocked={} reset_reason={} resets={} duplicates={}",
      d.at.ns,
      d.instrument.value,
      d.sequence,
      d.window_ms,
      d.bid_qty,
      d.ask_qty,
      d.buy_volume,
      d.sell_volume,
      d.valid,
      d.blocked,
      d.reset_reason,
      d.resets,
      d.duplicates);
}

void log_quote_decision(const QuoteDecision& d) noexcept {
  FASTMM_LOG_INFO(
      "mm_decision ts={} inst={} seq={} bid={} ask={} desired_bid={} desired_ask={} age_ns={} "
      "pos={} "
      "inv_age_ns={} mode={} ticker={} exit_reject={} reason={}",
      d.at,
      d.instrument.value,
      d.sequence,
      d.bid,
      d.ask,
      d.desired_bid,
      d.desired_ask,
      d.market_age,
      d.position,
      d.inventory_age,
      d.inventory_mode,
      d.ticker,
      d.exit_reject,
      d.reason);
}

}  // namespace fastmm
