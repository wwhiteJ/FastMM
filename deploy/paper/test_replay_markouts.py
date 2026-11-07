import unittest
import json
import tempfile
from pathlib import Path

from replay_markouts import SCALE, describe, markouts


class MarkoutTests(unittest.TestCase):
    def test_buys_and_sells_use_signed_notional_weighted_edges(self):
        rows = [dict(ts=0, sign=1, px=100*SCALE, qty=SCALE),
                dict(ts=1, sign=-1, px=110*SCALE, qty=2*SCALE)]
        samples = {}
        for horizon in (10, 100, 1000):
            target = horizon * 1_000_000
            samples[target] = (target, 1, 198*SCALE)
            samples[target+1] = (target+1, 2, 224*SCALE)
        result = markouts(rows, samples, 2_000_000_000)['100']
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['negative'], 2)
        self.assertEqual(result['usdt'], -5)
        self.assertEqual(result['bps'], -156.25)

    def test_incomplete_horizon_is_excluded(self):
        rows = [dict(ts=0, sign=1, px=100*SCALE, qty=SCALE)]
        samples = {h*1_000_000: (0, 1, 200*SCALE) for h in (10, 100, 1000)}
        result = markouts(rows, samples, 500_000_000)
        self.assertEqual(result['100']['count'], 1)
        self.assertEqual(result['1000']['count'], 0)
        self.assertIsNone(result['1000']['bps'])

    def test_missing_depth_mid_cannot_be_used_as_a_zero_price(self):
        summary = dict(realized_pnl=-12, unrealized_pnl=2, fills=1, maker_fills=1,
                       taker_fills=0, fees=1, net_pnl=-11, final_position=0,
                       orders=1, cancels=0, rejects=0, risk_rejects={},
                       inventory_max=1, max_drawdown=11)
        rows = [dict(ts=0, sign=1, px=100*SCALE, qty=SCALE, mid=0)]
        samples = {h*1_000_000: (0, 1, 200*SCALE) for h in (10, 100, 1000)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'summary.json').write_text(json.dumps(summary))
            result = describe(path, rows, samples, 2_000_000_000)
        self.assertEqual(result['gross_pnl'], -10)
        self.assertEqual(result['net_pnl'], -11)
        self.assertEqual(result['missing_depth_mid_fills'], 1)
        self.assertIsNone(result['depth_mid_capture'])
        self.assertIsNone(result['depth_mid_inventory_drift'])


if __name__ == '__main__':
    unittest.main()
