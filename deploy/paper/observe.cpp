// Independent simulated replay; it never sends orders to an exchange or changes the collector.
#include "replay_settings.hpp"

#include "fastmm/backtest/shadow.hpp"
#include "fastmm/backtest/shadow_rows_writer.hpp"
#include "fastmm/core/log.hpp"

#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

#include <atomic>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <string_view>

namespace {
std::atomic<bool> stopping{false};
void stop(int) {
  stopping.store(true);
}
void reopen(int) {
  fastmm::Logger::instance().request_reopen();
}

enum class FollowMode { Observe, Research };

void configure_follow(const fastmm::Config& config,
                      fastmm::bt::BacktestConfig& run,
                      const std::string& output,
                      FollowMode mode) {
  fastmm::paper::configure(config, run, output);
  run.keep_orders = false;
  if (mode == FollowMode::Observe) {
    run.params["gate_mode"] = "1";
    run.params["trace_quotes"] = "false";
    run.params_pinned.push_back("gate_mode");
    run.params_pinned.push_back("trace_quotes");
  }
}

void observe(const fastmm::Config& config, const std::string& output, FollowMode mode) {
  std::ofstream rows(output + "/rows.jsonl");
  rows.exceptions(std::ios::badbit | std::ios::failbit);
  fastmm::bt::ShadowRowsWriter writer(rows);
  fastmm::bt::ShadowOptions options;
  options.stop = &stopping;
  options.window = fastmm::seconds(30);
  options.report_path = output + "/shadow.json";
  options.configure = [&](fastmm::bt::BacktestConfig& run) {
    configure_follow(config, run, output, mode);
  };
  options.on_rows = [&](const fastmm::bt::ShadowRows& r) {
    writer.write(r);
    fastmm::Logger::instance().flush();
    const auto at = r.bar_end ? r.equity.ts[r.bar_end - 1] : 0;
    const auto temporary = output + "/health.tmp";
    std::ofstream health(temporary);
    health << "{\"mode\":\"" << (mode == FollowMode::Observe ? "observe" : "research")
           << "\",\"session_id\":" << r.session_id << ",\"as_of_ns\":" << at
           << ",\"log_dropped\":" << fastmm::Logger::instance().dropped() << "}\n";
    health.close();
    std::filesystem::rename(temporary, output + "/health.json");
  };
  fastmm::bt::Shadow shadow(options);
  shadow.follow(config.engine.journal_dir, config.engine.name);
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 3 && (argc != 4 || std::string_view(argv[3]) != "--research")) {
    std::fprintf(stderr, "usage: fastmm-paper-observe CONFIG OUTPUT_DIR [--research]\n");
    return 2;
  }
  try {
    const std::string output = argv[2];
    std::filesystem::create_directories(output);
    const int lock =
        ::open((output + "/observer.lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0640);
    if (lock < 0 || ::flock(lock, LOCK_EX | LOCK_NB))
      throw std::runtime_error("observer output already in use");
    if (!fastmm::Logger::instance().start_file(output + "/features.log", fastmm::LogLevel::Error))
      throw std::runtime_error("cannot open feature log");
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    std::signal(SIGHUP, reopen);
    observe(fastmm::Config::load(argv[1]),
            output,
            argc == 4 ? FollowMode::Research : FollowMode::Observe);
    fastmm::Logger::instance().stop();
    ::close(lock);
  } catch (const std::exception& e) {
    fastmm::Logger::instance().stop();
    std::fprintf(stderr, "%s\n", e.what());
    return 1;
  }
}
