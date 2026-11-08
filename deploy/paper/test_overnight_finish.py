import unittest

from overnight_finish import TRIAL_NS, TAIL_NS, comparison, validate_lock


class OvernightTests(unittest.TestCase):
    def test_does_not_close_collector_before_full_interval_and_tail(self):
        lock = {'start_ns': 1, 'end_ns': 1 + TRIAL_NS,
                'configs': {'baseline': {}, 'joint': {}}}
        with self.assertRaisesRegex(ValueError, 'not complete'):
            validate_lock(lock, lock['end_ns'] + TAIL_NS - 1)
        validate_lock(lock, lock['end_ns'] + TAIL_NS)
        lock['end_ns'] -= 1
        with self.assertRaisesRegex(ValueError, 'exactly ten'):
            validate_lock(lock, TRIAL_NS * 2)

    def test_invalid_ledger_cannot_claim_improvement(self):
        report = comparison({'baseline': {'errors': []},
                             'joint': {'errors': ['live_replay_mismatch']}})
        self.assertEqual(report['status'], 'invalid_or_incomplete')
        self.assertNotIn('improved_in_this_sample', report)

    def test_fee_saving_without_gross_improvement_is_not_a_pass(self):
        risk = {'errors': [], 'max_drawdown': '1', 'worst_episode_loss': '1',
                'position_integral_btc_seconds': '1'}
        results = {'baseline': {**risk, 'gross_pnl': '-1', 'net': '-3'},
                   'joint': {**risk, 'gross_pnl': '-2', 'net': '-2.5'}}
        report = comparison(results)
        self.assertEqual(report['gross_delta_usdt'], '-1')
        self.assertEqual(report['net_delta_usdt'], '0.5')
        self.assertFalse(report['improved_in_this_sample'])


if __name__ == '__main__':
    unittest.main()
