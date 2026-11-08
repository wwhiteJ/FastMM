#include "venue_test_util.hpp"

#include "fastmm/venues/binance/binance_md_parser.hpp"
#include "fastmm/venues/binance_usdm/binance_usdm_md_parser.hpp"

#include <algorithm>
#include <string>

using namespace fastmm;
using namespace fastmm::venues;
using namespace fastmm::venues::test;

namespace {
template <class Parser>
void check_direction(const char* event, const char* id_key) {
  TestUniverse universe;
  Parser parser(universe.symbols, VenueId{0});
  Scratch scratch;
  const Timestamp received{1'700'000'000'000'000'000LL};
  const PaddedJson ticker(
      R"({"stream":"btcusdt@bookTicker","data":{"e":"bookTicker","u":5,"s":"BTCUSDT","b":"1","B":"1","a":"2","A":"0.00000256","T":1,"E":1}})");
  for (const bool buyer_maker : {false, true}) {
    const PaddedJson json(std::string("{\"e\":\"") + event + "\",\"E\":1,\"" + id_key +
                          "\":7,\"s\":\"BTCUSDT\",\"p\":\"2\",\"q\":\"1\",\"T\":1,\"m\":" +
                          (buyer_maker ? "true}" : "false}"));
    for (const auto previous : {std::byte{0}, std::byte{1}, std::byte{255}}) {
      std::fill(scratch.span().begin(), scratch.span().end(), previous);
      REQUIRE(parser.decode(json.view(), received, Cycles{1}, scratch.span()).ok());
      const auto& trade = scratch.as<TradeMsg>();
      CHECK(trade.has_aggressor());
      CHECK(trade.aggressor == (buyer_maker ? Side::Sell : Side::Buy));
      CHECK(trade.trade_id == 7);
    }
    REQUIRE(parser.decode(ticker.view(), received, Cycles{1}, scratch.span()).ok());
    REQUIRE(scratch.span()[offsetof(TradeMsg, pad_)] == std::byte{1});
    REQUIRE(parser.decode(json.view(), received, Cycles{1}, scratch.span()).ok());
    CHECK(scratch.as<TradeMsg>().has_aggressor());
    CHECK(scratch.as<TradeMsg>().aggressor == (buyer_maker ? Side::Sell : Side::Buy));
  }
}
}  // namespace

TEST_CASE("binance.md: aggressor survives dirty buffers and bookTicker reuse") {
  check_direction<binance::BinanceMdParser>("trade", "t");
}

TEST_CASE("binance_usdm.md: aggressor survives dirty buffers and bookTicker reuse") {
  check_direction<binance_usdm::BinanceUsdmMdParser>("aggTrade", "a");
}
