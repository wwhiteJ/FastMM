#pragma once

#include "fastmm/core/enums.hpp"
#include "fastmm/core/fixed_point.hpp"
#include "fastmm/core/strong_id.hpp"
#include "fastmm/core/time.hpp"

#include <string_view>

namespace fastmm {

struct QuoteDecision {
  Timestamp at;
  InstrumentId instrument;
  std::uint64_t sequence;
  Price bid;
  Price ask;
  Price desired_bid;
  Price desired_ask;
  Duration market_age;
  Qty position;
  Duration inventory_age;
  int inventory_mode;
  bool ticker;
  RejectReason exit_reject;
  std::string_view reason;
};

void log_quote_decision(const QuoteDecision& decision) noexcept;

}  // namespace fastmm
