#include "test_support.hpp"

#include "fastmm/config/config.hpp"
#include "fastmm/core/risk.hpp"

using namespace fastmm;

TEST_CASE("core.risk: tiny exits require opt-in and a strictly closing derivative order") {
  Instrument inst{};
  inst.id = InstrumentId{0};
  inst.venue = VenueId{0};
  inst.flags = Instrument::kEnabled;
  inst.asset_class = AssetClass::Perpetual;
  inst.tick = *Price::from_decimal("0.1");
  inst.lot = *Qty::from_decimal("0.001");
  inst.min_qty = inst.lot;
  inst.min_notional = Notional::from_int(100);
  Position position{};
  position.qty = inst.lot;
  RiskInputs inputs{};
  inputs.position = &position;
  OrderIntent order{inst.id,
                    inst.venue,
                    Side::Sell,
                    OrderType::Limit,
                    Price::from_int(75000),
                    inst.lot,
                    TimeInForce::Ioc,
                    true};
  RiskLimits limits;
  RiskEngine original(limits);
  CHECK(original.check_new(order, inst, inputs) == RejectReason::BelowMinNotional);
  limits.reduce_only_min_notional_exempt = true;
  RiskEngine risk(limits);
  CHECK(risk.check_new(order, inst, inputs) == RejectReason::None);
  SUBCASE("unflagged entry") {
    order.reduce_only = false;
  }
  SUBCASE("wrong side") {
    order.side = Side::Buy;
  }
  SUBCASE("flat") {
    position.qty = {};
  }
  SUBCASE("already reserved by another exit") {
    inputs.open_same_side = inst.lot;
  }
  SUBCASE("spot venue") {
    inst.asset_class = AssetClass::Spot;
  }
  CHECK(risk.check_new(order, inst, inputs) == RejectReason::BelowMinNotional);
}

TEST_CASE("core.config: reduce-only notional policy survives journal configuration round-trip") {
  const auto config = Config::parse(R"(
[engine]
name = "test"
[venues.sim]
kind = "sim"
[[instruments]]
venue = "sim"
symbol = "BTCUSDT"
tick = "0.1"
lot = "0.001"
[strategy]
name = "basic_mm"
[risk]
reduce_only_min_notional_exempt = true
)");
  CHECK(config.risk_limits().reduce_only_min_notional_exempt);
  CHECK(Config::parse(config.effective_toml()).risk_limits().reduce_only_min_notional_exempt);
  CHECK(config.redacted().find("reduce_only_min_notional_exempt") != std::string::npos);
}
