#include "fastmm/core/quote_decision.hpp"

#include "fastmm/core/log.hpp"

#include <fmt/format.h>

namespace fastmm {
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
