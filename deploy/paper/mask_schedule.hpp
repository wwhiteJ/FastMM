#pragma once

#include "fastmm/backtest/multiverse.hpp"
#include "fastmm/sim/param_schedule.hpp"
#include "fastmm/strategies/builtin.hpp"

#include <fstream>
#include <memory>
#include <stdexcept>

namespace fastmm::paper {

// Streams a precomputed, hashed schedule. Only one parameter update is queued ahead of the clock.
class MaskSchedule {
 public:
  MaskSchedule(const std::string& path,
               std::unique_ptr<ParamPublisher> publisher,
               Timestamp start,
               Timestamp stop)
      : input_(path), publisher_(std::move(publisher)), start_(start), stop_(stop) {
    std::string header;
    if (!std::getline(input_, header) || header != "ts_ns,blocked")
      throw std::runtime_error("invalid mask schedule header");
    next_ = read();
    if (next_ != start_ || mask_ != 0)
      throw std::runtime_error("mask schedule must start clear at entry_start_ns");
  }
  void attach(bt::BacktestSession& session) {
    schedule_.set_refill(session.backend().clock, this, refill, next_);
    session.set_param_schedule(&schedule_);
  }

 private:
  Timestamp read() {
    std::int64_t ts = 0;
    char separator = 0;
    if (!(input_ >> ts)) {
      if (!input_.eof()) throw std::runtime_error("invalid mask timestamp");
      if (last_ != stop_ || mask_ != 0)
        throw std::runtime_error("mask schedule must finish clear at entry_stop_ns");
      return Timestamp::max();
    }
    if (!(input_ >> separator >> mask_) || separator != ',' || mask_ < 0 || mask_ > 2 ||
        Timestamp{ts} < start_ || Timestamp{ts} > stop_ || (last_.valid() && ts < last_.ns))
      throw std::runtime_error("unordered or invalid mask schedule");
    last_ = Timestamp{ts};
    return last_;
  }
  static Timestamp refill(void* context, Timestamp due) {
    auto& self = *static_cast<MaskSchedule*>(context);
    ParamUpdateMsg update{};
    if (auto error = self.publisher_->build({{"gate_forced_mask", std::to_string(self.mask_)}},
                                            ParamPublisher::kAllInstruments,
                                            update))
      throw std::runtime_error(*error);
    self.schedule_.at(due, update);
    return self.read();
  }
  std::ifstream input_;
  std::unique_ptr<ParamPublisher> publisher_;
  sim::ParamSchedule schedule_;
  Timestamp start_, stop_, next_{}, last_{};
  int mask_ = 0;
};

inline bt::MultiverseFactory replay_factory(const std::string& path) {
  register_builtin_strategies(StrategyRegistry::instance());
  return [path](std::size_t, const bt::BacktestConfig& original, bt::MdSource& source) {
    auto cfg = original;
    cfg.keep_orders = true;  // point_config deliberately discards rows for ordinary sweeps.
    const auto* entry = StrategyRegistry::instance().find("basic_mm");
    if (!entry || cfg.strategy != "basic_mm" ||
        (!path.empty() && cfg.params.at("gate_rule") != "3"))
      throw std::runtime_error("mask schedule requires the basic_mm scheduled research rule");
    bt::MultiverseRun run;
    run.session = std::make_unique<bt::BacktestSession>(cfg, &source, entry->schema);
    run.runner =
        StrategyRegistry::instance().make(entry->name, TransportKind::Sim, run.session->deps());
    if (!run.runner) throw std::runtime_error("cannot build scheduled research runner");
    run.hooks = run.session->backend().hooks;
    run.strategy_name = std::string(entry->name);
    if (path.empty()) return run;
    auto schedule = std::make_shared<MaskSchedule>(
        path,
        entry->publisher(ParamSink{}, cfg.params, cfg.instruments.size()),
        Timestamp{std::stoll(cfg.params.at("entry_start_ns"))},
        Timestamp{std::stoll(cfg.params.at("entry_stop_ns"))});
    schedule->attach(*run.session);
    run.on_end = [schedule] {};
    return run;
  };
}

}  // namespace fastmm::paper
