#pragma once

#include "fastmm/backtest/backtest_config.hpp"

#include <fstream>
#include <stdexcept>

namespace fastmm::paper {

inline void apply_model(const bt::BacktestConfig& desired, bt::BacktestConfig& run) {
  auto& dst = run.transport;
  const auto& src = desired.transport;
  dst.order_out = src.order_out;
  dst.ack_in = src.ack_in;
  dst.md_in = src.md_in;
  dst.md_recorded_arrival = src.md_recorded_arrival;
  dst.cancel_latency = src.cancel_latency;
  dst.cancel_out = src.cancel_out;
  dst.fill_latency = src.fill_latency;
  dst.fill_in = src.fill_in;
  dst.order_service = src.order_service;
  dst.venues = src.venues;
  dst.fill_model = src.fill_model;
  dst.queue_conservatism_bps = src.queue_conservatism_bps;
  run.engine.queue_conservatism_bps = src.queue_conservatism_bps;
}

inline void write_model(const bt::BacktestConfig& run, const std::string& output) {
  const auto& t = run.transport;
  const auto fee = t.fees.schedule(InstrumentId{0});
  std::ofstream out(output + "/effective_model.json");
  out.exceptions(std::ios::badbit | std::ios::failbit);
  out << "{\"entry_ns\":" << t.order_out.fixed.ns
      << ",\"entry_jitter_ns\":" << t.order_out.jitter.ns
      << ",\"response_ns\":" << t.ack_in.fixed.ns
      << ",\"response_jitter_ns\":" << t.ack_in.jitter.ns
      << ",\"cancel_ns\":" << (t.cancel_latency ? t.cancel_out.fixed.ns : t.order_out.fixed.ns)
      << ",\"cancel_jitter_ns\":"
      << (t.cancel_latency ? t.cancel_out.jitter.ns : t.order_out.jitter.ns)
      << ",\"queue_conservatism_bps\":" << t.queue_conservatism_bps
      << ",\"maker_cbps\":" << fee.maker_cbps << ",\"taker_cbps\":" << fee.taker_cbps
      << ",\"recorded_arrival\":" << (t.md_recorded_arrival ? "true" : "false")
      << ",\"fill_model\":" << static_cast<int>(t.fill_model) << ",\"seed\":" << run.seed << "}\n";
}

inline void configure(const Config& config, bt::BacktestConfig& run, const std::string& output) {
  const auto desired = bt::BacktestConfig::from_config(config);
  if (!desired.warnings.empty()) throw std::runtime_error(desired.warnings.front());
  if (run.instruments.size() != 1 || desired.instruments.size() != 1 ||
      run.instruments.get(InstrumentId{0}).symbol.view() != "BTCUSDT" ||
      desired.instruments.get(InstrumentId{0}).symbol.view() != "BTCUSDT")
    throw std::runtime_error("paper calibration requires one BTCUSDT instrument");
  if (!desired.transport.venues.empty())
    throw std::runtime_error("paper calibration does not accept per-venue latency overrides");
  run.params = desired.params;
  for (const auto& [name, value] : run.params) {
    static_cast<void>(value);
    run.params_pinned.push_back(name);
  }
  run.engine.risk = desired.engine.risk;
  run.engine.quotes = desired.engine.quotes;
  apply_model(desired, run);
  run.transport.fees = fee_table(config, &run.instruments, &run.venue_names);
  run.engine.fees = run.transport.fees;
  run.keep_orders = true;
  write_model(run, output);
  std::ofstream config_out(output + "/requested.toml");
  config_out.exceptions(std::ios::badbit | std::ios::failbit);
  config_out << config.effective_toml();
}

}  // namespace fastmm::paper
