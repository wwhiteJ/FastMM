import unittest
from decimal import Decimal

from accounting import funding_payments, summarize
from report import fetch_funding


class AccountingTests(unittest.TestCase):
    def test_funding_uses_position_before_settlement_and_dedicated_sign(self):
        fills = [{"ts_ns": 999_999, "side": 0, "qty_raw": 200_000},
                 {"ts_ns": 1_000_000, "side": 1, "qty_raw": 100_000}]
        event = {"fundingTime": 1, "markPrice": "75000", "fundingRate": "0.0001"}
        payment = funding_payments(fills, [event], 2_000_000)[0]
        self.assertEqual(Decimal(payment["amount_usdt"]), Decimal("-0.015"))
        event["fundingRate"] = "-0.0001"
        self.assertEqual(Decimal(funding_payments(fills, [event], 2_000_000)[0]["amount_usdt"]), Decimal("0.015"))

    def test_restart_uses_only_current_session_and_preserves_unrealized(self):
        rows = [dict(type="fill", session_id=1, ts_ns=1, side=0, qty_raw=100_000,
                     price_raw=7_500_000_000_000, fee_raw=1_350_000, liquidity=1),
                dict(type="equity", session_id=1, ts_ns=2, realized_raw=100_000_000,
                     unrealized_raw=0, fees_raw=1_350_000, position_raw=100_000, mid_raw=1),
                dict(type="equity", session_id=2, ts_ns=3, realized_raw=0,
                     unrealized_raw=-10_000_000, fees_raw=2_000_000, position_raw=-100_000, mid_raw=1)]
        result = summarize(rows, [], "100000")
        self.assertEqual(result["fills"], 0)
        self.assertEqual(Decimal(result["net_including_funding_usdt"]), Decimal("-0.12"))
        self.assertEqual(Decimal(result["sampled_max_drawdown_usdt"]), Decimal("0.12"))

    def test_funding_failure_response_is_not_zero_funding(self):
        with self.assertRaises(ValueError):
            fetch_funding(0, 10, lambda _: {"code": -1003})

    def test_short_pays_negative_rate_and_future_settlement_is_excluded(self):
        fills = [{"ts_ns": 1, "side": 1, "qty_raw": 100_000}]
        events = [dict(fundingTime=1, markPrice="75000", fundingRate="-0.0001"),
                  dict(fundingTime=3, markPrice="75000", fundingRate="-0.0001")]
        payments = funding_payments(fills, events, 2_000_000)
        self.assertEqual(len(payments), 1)
        self.assertEqual(Decimal(payments[0]["amount_usdt"]), Decimal("-0.0075"))


if __name__ == "__main__":
    unittest.main()
