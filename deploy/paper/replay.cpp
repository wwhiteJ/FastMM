// Offline replay of a closed collector session using the exact paper Shadow path.
#include "fastmm/backtest/shadow.hpp"
#include "fastmm/config/config.hpp"
#include "fastmm/core/log.hpp"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <stdexcept>

namespace {
void replay(const char* journal, const char* config_path, const char* output) {
  const fastmm::Config config = fastmm::Config::load(config_path);
  fastmm::bt::ShadowOptions options;
  options.window = fastmm::seconds(30);
  options.settle = fastmm::seconds(10);
  options.keep_results = true;
  options.configure = [&](fastmm::bt::BacktestConfig& run) {
    run.params = config.strategy.params;
    for (const auto& [name, value] : run.params) {
      static_cast<void>(value);
      run.params_pinned.push_back(name);
    }
    run.engine.risk.reduce_only_min_notional_exempt = config.risk.reduce_only_min_notional_exempt;
  };
  fastmm::bt::Shadow shadow(options);
  const auto session = shadow.session(journal);
  if (session.results.size() != 1 || session.stopped || session.seq_gaps != 0)
    throw std::runtime_error("incomplete or unexpected shadow session");
  if (!session.results.front().write_all(output))
    throw std::runtime_error("failed to write diagnostic results");
  std::ofstream report(std::string(output) + "/shadow_report.json");
  report << shadow.report_json();
  std::printf("session=%llu fills=%zu windows=%zu\n",
              static_cast<unsigned long long>(session.session_id),
              session.results.front().fills.size(),
              shadow.windows());
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::fprintf(stderr, "usage: fastmm-paper-replay CLOSED_JOURNAL PARAM_CONFIG OUTPUT_DIR\n");
    return 2;
  }
  try {
    std::filesystem::create_directories(argv[3]);
    const std::string path = std::string(argv[3]) + "/decisions.log";
    if (!fastmm::Logger::instance().start_file(path, fastmm::LogLevel::Error))
      throw std::runtime_error("cannot open decisions log");
    replay(argv[1], argv[2], argv[3]);
    fastmm::Logger::instance().stop();
    std::ofstream metadata(std::string(argv[3]) + "/diagnostics.json");
    metadata << "{\"log_dropped\":" << fastmm::Logger::instance().dropped() << "}\n";
  } catch (const std::exception& error) {
    fastmm::Logger::instance().stop();
    std::fprintf(stderr, "%s\n", error.what());
    return 1;
  }
}
