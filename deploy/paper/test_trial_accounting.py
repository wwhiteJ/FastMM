import unittest
from decimal import Decimal

from trial_accounting import evaluate_trial

NS = 1_000_000_000
SCALE = 100_000_000


def bar(seconds, gross, fees, session=1):
    return dict(type="equity", session_id=session, ts_ns=seconds * NS,
                realized_raw=0, unrealized_raw=int(Decimal(gross) * SCALE),
                fees_raw=int(Decimal(fees) * SCALE), position_raw=0, mid_raw=SCALE)


class TrialAccountingTests(unittest.TestCase):
    def test_early_profit_does_not_stop_and_later_profit_cannot_change_fixed_result(self):
        early = [bar(30, "1", "0.1")]
        self.assertFalse(evaluate_trial(early, 1, 30, 60, [])["stop_optimizing"])
        rows = early + [bar(60, "0.1", "0.2"), bar(90, "100", "0.3")]
        result = evaluate_trial(rows, 1, 30, 60, [])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(Decimal(result["net_including_funding_usdt"]), Decimal("-0.1"))
        self.assertFalse(result["stop_optimizing"])

    def test_all_first_interval_costs_and_floating_pnl_are_counted(self):
        result = evaluate_trial([bar(30, "-1", "0.2"), bar(60, "0.4", "0.3")], 1, 30, 60, [])
        self.assertEqual(result["start_ns"], 0)
        self.assertEqual(result["elapsed_s"], 60)
        self.assertEqual(Decimal(result["net_including_funding_usdt"]), Decimal("0.1"))
        self.assertTrue(result["stop_optimizing"])

    def test_sessions_are_isolated_and_missing_end_is_rejected(self):
        rows = [bar(30, "500", "0", 2), bar(30, "0", "0"), bar(90, "1", "0")]
        with self.assertRaises(ValueError):
            evaluate_trial(rows, 1, 30, 60, [])
        self.assertEqual(evaluate_trial(rows, 3, 30, 60, [])["state"], "waiting_for_session")

    def test_zero_net_is_not_positive(self):
        result = evaluate_trial([bar(30, "0", "0"), bar(60, "1", "1")], 1, 30, 60, [])
        self.assertFalse(result["stop_optimizing"])

    def test_funding_in_window_is_deducted(self):
        fill = dict(type="fill", session_id=1, ts_ns=NS, side=0, qty_raw=SCALE,
                    price_raw=100 * SCALE, fee_raw=0, liquidity=1)
        history = [dict(fundingTime=60_000, markPrice="100", fundingRate="0.01")]
        result = evaluate_trial([fill, bar(30, "0", "0"), bar(60, "0.5", "0")],
                                1, 30, 60, history)
        self.assertEqual(Decimal(result["net_including_funding_usdt"]), Decimal("-0.5"))
        self.assertFalse(result["stop_optimizing"])


if __name__ == "__main__":
    unittest.main()
