#pragma once

#include "fastmm/core/messages.hpp"
#include "fastmm/core/time.hpp"
#include "fastmm/strategies/quoting.hpp"

namespace fastmm {

// Only locally delivered updates enter this cache. Binance depth and ticker share update ids.
class LatestBbo {
 public:
  template <class Book>
  bool depth(const Book& book, Timestamp received) noexcept {
    return accept(book.best_bid(),
                  book.best_ask(),
                  book.seq(),
                  book.last_update(),
                  received,
                  false,
                  book.is_valid());
  }
  bool ticker(const BookTickerMsg& m, Timestamp received) noexcept {
    return accept({m.bid_px, m.bid_qty},
                  {m.ask_px, m.ask_qty},
                  m.hdr.venue_seq,
                  m.hdr.exch_ts.valid() ? m.hdr.exch_ts : m.hdr.recv_ts,
                  received,
                  true,
                  true);
  }
  [[nodiscard]] bool fresh(Timestamp now, Duration max_age) const noexcept {
    return valid_ && (max_age.ns == 0 || now - received_ <= max_age);
  }
  [[nodiscard]] Price mid() const noexcept { return fastmm::mid(bid_.price, ask_.price); }
  [[nodiscard]] Price bid() const noexcept { return bid_.price; }
  [[nodiscard]] Price ask() const noexcept { return ask_.price; }
  [[nodiscard]] Timestamp received() const noexcept { return received_; }
  [[nodiscard]] std::uint64_t sequence() const noexcept { return seq_; }
  [[nodiscard]] bool from_ticker() const noexcept { return ticker_; }

 private:
  bool accept(Level bid,
              Level ask,
              std::uint64_t seq,
              Timestamp exchange,
              Timestamp received,
              bool ticker,
              bool valid) noexcept {
    // The depth engine validates its own sequence, including snapshot resets in chained data.
    // Compare common ids when a ticker is involved; depth-to-depth follows engine time.
    if (seen_ && ((ticker || ticker_) && seq != 0 && seq_ != 0
                      ? seq <= seq_
                      : (exchange < exchange_ || (exchange == exchange_ && ticker_ && !ticker))))
      return false;
    seen_ = true;
    seq_ = seq;
    exchange_ = exchange;
    received_ = received;
    ticker_ = ticker;
    bid_ = bid;
    ask_ = ask;
    valid_ = valid && bid.price.is_positive() && ask.price > bid.price && bid.qty.is_positive() &&
             ask.qty.is_positive();
    return true;
  }

  Level bid_{};
  Level ask_{};
  std::uint64_t seq_ = 0;
  Timestamp exchange_{};
  Timestamp received_{};
  bool seen_ = false;
  bool valid_ = false;
  bool ticker_ = false;
};

}  // namespace fastmm
