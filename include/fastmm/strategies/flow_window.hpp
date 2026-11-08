#pragma once

#include "fastmm/core/messages.hpp"

#include <array>
#include <cstddef>
#include <limits>

namespace fastmm {

inline constexpr std::array<std::int64_t, 11> kRetentionWindowsMs = {
    100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200, 60000};

struct FlowTotals {
  std::int64_t buy = 0;
  std::int64_t sell = 0;
  bool valid = false;
};

// Exact consumption-time windows. Prefix sums permit O(log n) queries without time buckets.
// Storage is allocated during strategy startup, never while handling market events.
template <std::size_t Capacity>
class FlowWindow {
 public:
  static constexpr Duration kHistory = milliseconds(kRetentionWindowsMs.back());
  enum class ResetReason {
    Startup,
    Connection,
    Clock,
    TradeGap,
    CapacityExceeded,
    QuantityOverflow
  };

  void reset(Timestamp now, ResetReason reason) noexcept {
    begin_ = size_ = 0;
    base_buy_ = base_sell_ = total_buy_ = total_sell_ = 0;
    started_ = last_time_ = now;
    reason_ = reason;
    ++resets_;
  }

  void advance(Timestamp now) noexcept {
    if (!started_.valid()) reset(now, ResetReason::Startup);
    if (now < last_time_) reset(now, ResetReason::Clock);
    last_time_ = now;
    while (size_ && entry(0).at <= now - kHistory) {
      base_buy_ = entry(0).buy;
      base_sell_ = entry(0).sell;
      begin_ = (begin_ + 1) % Capacity;
      --size_;
    }
  }

  void trade(Timestamp now, const TradeMsg& m) noexcept {
    advance(now);
    if (!m.qty.is_positive() || !m.has_aggressor()) return;
    if (m.trade_id && m.trade_id <= last_trade_id_) {
      ++duplicates_;
      return;
    }
    if (last_trade_id_ && m.trade_id > last_trade_id_ + 1) reset(now, ResetReason::TradeGap);
    if (m.trade_id) last_trade_id_ = m.trade_id;
    if (size_ == Capacity) reset(now, ResetReason::CapacityExceeded);
    const auto maximum = std::numeric_limits<std::int64_t>::max();
    if (m.qty.raw > maximum - total_buy_ - total_sell_) reset(now, ResetReason::QuantityOverflow);
    (m.aggressor == Side::Buy ? total_buy_ : total_sell_) += m.qty.raw;
    entries_[(begin_ + size_) % Capacity] = {now, total_buy_, total_sell_};
    ++size_;
  }

  [[nodiscard]] FlowTotals totals(Timestamp now, Duration window) const noexcept {
    if (!started_.valid() || now < last_time_ || window.ns <= 0 || window > kHistory ||
        now - started_ < window)
      return {};
    const Timestamp cutoff = now - window;
    std::size_t low = 0, high = size_;
    while (low < high) {
      const std::size_t middle = low + (high - low) / 2;
      if (entry(middle).at <= cutoff)
        low = middle + 1;
      else
        high = middle;
    }
    const auto buy = low ? entry(low - 1).buy : base_buy_;
    const auto sell = low ? entry(low - 1).sell : base_sell_;
    return {total_buy_ - buy, total_sell_ - sell, true};
  }

  [[nodiscard]] ResetReason reason() const noexcept { return reason_; }
  [[nodiscard]] std::uint64_t resets() const noexcept { return resets_; }
  [[nodiscard]] std::uint64_t duplicates() const noexcept { return duplicates_; }

 private:
  struct Entry {
    Timestamp at;
    std::int64_t buy;
    std::int64_t sell;
  };
  [[nodiscard]] const Entry& entry(std::size_t i) const noexcept {
    return entries_[(begin_ + i) % Capacity];
  }
  std::array<Entry, Capacity> entries_{};
  std::size_t begin_ = 0, size_ = 0;
  std::int64_t base_buy_ = 0, base_sell_ = 0, total_buy_ = 0, total_sell_ = 0;
  Timestamp started_{}, last_time_{};
  std::uint64_t last_trade_id_ = 0, resets_ = 0, duplicates_ = 0;
  ResetReason reason_ = ResetReason::Startup;
};

}  // namespace fastmm
