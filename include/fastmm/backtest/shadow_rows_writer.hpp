#pragma once

#include "fastmm/backtest/shadow.hpp"

#include <ostream>

namespace fastmm::bt {

// Cold-path export for the baseline simulation; the caller owns the output stream.
class ShadowRowsWriter {
 public:
  explicit ShadowRowsWriter(std::ostream& out) : out_(out) {}

  void write(const ShadowRows& rows) {
    if (rows.point != 0) return;
    const FillRows& f = rows.fills;
    for (std::size_t i = rows.fill_begin; i < rows.fill_end; ++i) {
      out_ << "{\"type\":\"fill\",\"session_id\":" << rows.session_id
           << ",\"ts_ns\":" << f.ts[i] << ",\"instrument\":" << f.instrument[i]
           << ",\"side\":" << static_cast<int>(f.side[i]) << ",\"price_raw\":" << f.price[i]
           << ",\"qty_raw\":" << f.qty[i] << ",\"fee_raw\":" << f.fee[i]
           << ",\"liquidity\":" << static_cast<int>(f.liquidity[i]) << "}\n";
    }
    const EquityRows& e = rows.equity;
    for (std::size_t i = rows.bar_begin; i < rows.bar_end; ++i) {
      out_ << "{\"type\":\"equity\",\"session_id\":" << rows.session_id
           << ",\"ts_ns\":" << e.ts[i] << ",\"realized_raw\":" << e.realized[i]
           << ",\"unrealized_raw\":" << e.unrealized[i] << ",\"fees_raw\":" << e.fees[i]
           << ",\"position_raw\":" << e.position[i] << ",\"mid_raw\":" << e.mid[i] << "}\n";
    }
    out_.flush();
  }

 private:
  std::ostream& out_;
};

}  // namespace fastmm::bt
