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

struct RetentionObservation {
  Timestamp at;
  InstrumentId instrument;
  std::uint64_t sequence;
  std::int64_t window_ms;
  std::int64_t bid_qty, ask_qty, buy_volume, sell_volume;
  bool valid;
  unsigned blocked;
  int reset_reason;
  std::uint64_t resets, duplicates;
};
void log_retention_observation(const RetentionObservation& observation) noexcept;

struct RetentionEquity {
  Timestamp at;
  InstrumentId instrument;
  Qty position;
  Price average, bid, ask;
  Notional realized, fees;
  bool valid_mark;
};
void log_retention_equity(const RetentionEquity& equity) noexcept;

}  // namespace fastmm
