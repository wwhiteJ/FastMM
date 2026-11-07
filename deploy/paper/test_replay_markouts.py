import unittest

from replay_markouts import SCALE, markouts


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


if __name__ == '__main__':
    unittest.main()
