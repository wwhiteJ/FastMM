import csv
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from decimal import Decimal as D
from fractions import Fraction as F
from pathlib import Path

import numpy as np

from retention.artifacts import HOUR_NS, DAY_NS, identity, override_toml, seed, whole_days, write_json
from retention.controls import random_schedule, write_schedule
from retention.features import import_features, quantiles, pause_cycles, generate_candidates
from retention.ledger import cash_metrics, funding_events, risk_ok
from retention.runner import assert_effective, scenario_config, strategy_parameters
from retention.selection import choose, leave_day_out, point_checks
from retention.statistics import bootstrap, plan_test, select_block, test_intervals
from retention_cli import export_config, funding


class RetentionContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_exact_nearest_rank_is_not_interpolation(self):
        values, count = quantiles(Counter({F(1, 3): 5, F(2, 3): 4, F(1): 1}), [.5, .75, .9])
        self.assertEqual((values, count), ([F(1, 3), F(2, 3), F(2, 3)], 10))
        with self.assertRaisesRegex(ValueError, "insufficient"):
            quantiles(Counter(), [.5])

    def test_toml_override_preserves_unrelated_sections(self):
        original = '[strategy.params]\nx = 3\ny = true\n[risk]\nx = 9'
        modified = override_toml(original, {'strategy.params': {'x': 7, 'z': False}})
        self.assertIn('y = true\n', modified)
        self.assertIn('[risk]\nx = 9', modified)
        self.assertIn('z = false\n', modified)

    def test_artifact_cannot_be_overwritten(self):
        path = self.root / 'lock.json'
        write_json(path, {'a': 1})
        write_json(path, {'a': 1})
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            write_json(path, {'a': 2})

    def test_seed_is_stable_and_phase_specific(self):
        self.assertEqual(seed('a', 'b', 'test', 'pause', 1), seed('a', 'b', 'test', 'pause', 1))
        self.assertNotEqual(seed('a', 'b', 'train', 'pause', 1), seed('a', 'b', 'test', 'pause', 1))
        self.assertEqual(len(identity({'a': 1})), 64)

    def test_complete_utc_days_required(self):
        self.assertEqual(whole_days(DAY_NS, 8 * DAY_NS, 7), 7)
        with self.assertRaises(ValueError):
            whole_days(DAY_NS + 1, 8 * DAY_NS, 7)

    def test_conflicting_causal_feature_snapshot_is_rejected(self):
        log = self.root / 'observations.log'
        row = 'retention ts=100000000 inst=0 seq=7 w_ms=100 bid_q=3 ask_q=1 buy_v=2 sell_v=4 valid=true blocked=0 resets=1\n'
        log.write_text(row + row)
        db = self.root / 'features.sqlite'
        self.assertEqual(import_features(db, [log])['rows_stored'], 1)
        log.write_text(row.replace('seq=7', 'seq=8'))
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            import_features(db, [log])

    def test_grid_deduplicates_aliases_and_keeps_original_neighbors(self):
        log = self.root / 'features.log'
        rows = []
        for at in range(100_000_000, 1_000_000_001, 100_000_000):
            for window in (100, 60000):
                rows.append(f'retention ts={at} inst=0 seq=7 w_ms={window} bid_q=3 ask_q=1 buy_v=2 sell_v=4 valid=true blocked=0 resets=1\n')
        log.write_text(''.join(rows))
        db = self.root / 'features.sqlite'
        import_features(db, [log])
        protocol = {'candidate_generation': {'window_ms': [100, 60000], 'positive_sample_quantile_ranks': [.5, .75, .9]}}
        grid = generate_candidates(db, protocol, 100_000_000, 1_100_000_000)
        self.assertEqual(len(grid['candidates']), 8)
        self.assertTrue(all(c['neighbors'] for c in grid['candidates']))
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute('DELETE FROM features WHERE ts=500000000')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            generate_candidates(db, protocol, 100_000_000, 1_100_000_000)

    def test_pause_cycles_exclude_censored_and_invalid_intervals(self):
        log = self.root / 'cycles.log'
        states = [(1, 1, 1), (2, 0, 1), (3, 2, 1), (5, 0, 1),
                  (6, 1, 0), (7, 0, 1), (8, 1, 1), (10, 2, 1), (11, 0, 1)]
        log.write_text(''.join(f'retention ts={t} valid={valid} blocked={side} resets=1\n' for t, side, valid in states))
        self.assertEqual(pause_cycles(log, 0, 12), [[1, 2, 2], [1, 1, 2], [0, 2, 1]])

    def test_random_control_does_not_read_market_and_is_deterministic(self):
        cycles = [[0, 1, 5], [3, 2, 7]]
        first = random_schedule(cycles, 100, 1000, 42)
        self.assertEqual(first, random_schedule(cycles, 100, 1000, 42))
        self.assertEqual((first[0], first[-1]), ((100, 0), (1000, 0)))
        self.assertTrue(all(a[0] <= b[0] for a, b in zip(first, first[1:])))
        path = self.root / 'schedule.csv'
        write_schedule(path, first)
        write_schedule(path, first)
        with self.assertRaisesRegex(ValueError, 'changed'):
            write_schedule(path, [(100, 0), (1000, 0)])

    def test_ledger_counts_fees_funding_and_open_inventory_hourly(self):
        hour = HOUR_NS
        events = [(10, D('.002'), D('-200.04'), {}), (hour + 20, D('-.002'), D('201.95'), {})]
        payments = [(hour, D(0), D('-.01'), None)]
        marks = {hour: {'valid': 1, 'bid': 10049900000000, 'ask': 10050100000000},
                 2 * hour: {'valid': 1, 'bid': 10049900000000, 'ask': 10050100000000}}
        result = cash_metrics(events + payments, marks, 0, 2 * hour, D('.006'))
        self.assertAlmostEqual(sum(result['hourly']), 1.9)
        self.assertAlmostEqual(result['hourly'][0], .96)
        self.assertEqual(result['errors'], [])

    def test_missing_endpoint_and_residual_cannot_pass(self):
        event = [(10, D('.001'), D('-100'), {})]
        with self.assertRaisesRegex(ValueError, 'missing UTC-hour'):
            cash_metrics(event, {}, 0, HOUR_NS, D('.006'))
        marks = {HOUR_NS: {'valid': 1, 'bid': 10000000000000, 'ask': 10000000000000}}
        self.assertIn('incomplete_exit', cash_metrics(event, marks, 0, HOUR_NS, D('.006'))['errors'])

    def test_funding_uses_position_strictly_before_settlement(self):
        fills = [(5, D('.001'), D(0), {}), (10_000_000, D('.002'), D(0), {})]
        history = {'start_ns': 0, 'end_ns': HOUR_NS, 'verified': True,
                   'events': [{'fundingTime': 10, 'markPrice': '100000', 'fundingRate': '.0001'}]}
        self.assertEqual(funding_events(fills, history, 0, HOUR_NS)[0][2], D('-.01'))

    def test_baseline_and_active_parameters_have_explicit_phase_bounds(self):
        candidate = {'parameters': {'window_ms': 400, 'a': {'numerator': 1, 'denominator': 3},
                                    'b': {'numerator': 1, 'denominator': 2}}}
        manifest = {'start_ns': DAY_NS, 'end_ns': 8 * DAY_NS}
        params = strategy_parameters(candidate, manifest, 0)
        self.assertEqual(params['entry_stop_ns'], 8 * DAY_NS - 70_000_000_000)
        self.assertEqual(params['gate_trade_den'], 3)
        self.assertNotIn('gate_trade_den', strategy_parameters(None, manifest, 0))

    def test_scenario_overrides_are_verified_against_actual_engine(self):
        protocol = json.loads((Path(__file__).parent / 'retention_protocol.json').read_text())
        text = (Path(__file__).parent / 'btcusdt.toml').read_text()
        _, scenario = scenario_config(text, protocol, 'S3')
        actual = {'entry_ns': 50_000_000, 'cancel_ns': 50_000_000, 'response_ns': 50_000_000,
                  'queue_conservatism_bps': 10000, 'maker_cbps': 180, 'taker_cbps': 450,
                  'entry_jitter_ns': 1000000, 'cancel_jitter_ns': 1000000,
                  'response_jitter_ns': 1000000, 'recorded_arrival': True}
        assert_effective(actual, scenario)
        actual['cancel_ns'] = 5_000_000
        with self.assertRaisesRegex(ValueError, 'cancel_ns'):
            assert_effective(actual, scenario)

    def test_risk_cannot_improve_by_dropping_stopped_interval(self):
        baseline = {'errors': ['risk_budget_exhausted'], 'max_drawdown': '1',
                    'worst_episode_loss': '1', 'position_integral_btc_seconds': '1'}
        gate = {**baseline, 'errors': []}
        self.assertFalse(risk_ok(gate, baseline))

    def test_training_requires_leave_one_day_and_neighbor_stability(self):
        baseline = {'errors': [], 'hourly': [-1.] * 168, 'max_drawdown': '10', 'worst_episode_loss': '10',
                    'position_integral_btc_seconds': '10', 'fills': 1, 'notional': '100', 'cancel_requests': 1}
        gate = {**baseline, 'hourly': [.1] * 168}
        candidate = {'id': 'a', 'neighbors': ['b']}
        neighbor = {**baseline, 'hourly': [-2.] * 168}
        self.assertEqual(choose([candidate], {'B0': baseline, 'a': gate, 'b': neighbor})['status'], 'no_candidate')
        self.assertGreater(leave_day_out([1.] * 168), 0)

    def test_invalid_training_candidate_is_excluded_without_fabricated_zero_hours(self):
        result = choose([{'id': 'bad', 'neighbors': []}],
                        {'B0': {'errors': []}, 'bad': {'errors': ['incomplete_exit']}})
        self.assertEqual(result['status'], 'no_candidate')
        self.assertIsNone(result['scores'][0]['objective'])

    def test_validation_requires_complete_controls_and_beating_random_mean(self):
        baseline = {'errors': [], 'hourly': [-1.] * 168, 'max_drawdown': '10',
                    'worst_episode_loss': '10', 'position_integral_btc_seconds': '10', 'fills': 1}
        gate = {**baseline, 'hourly': [.1] * 168, 'net': '16.8'}
        results = {'B0': baseline, 'gate': gate, 'neighbors': {}, 'stress': {}, 'random': []}
        self.assertFalse(point_checks(results, {'neighbors': []})['pass'])
        results['stress'] = {f'S{i}': {'B0': baseline, 'gate': gate} for i in range(1, 6)}
        results['random'] = [{**gate, 'hourly': [.2] * 168} for _ in range(20)]
        self.assertEqual(point_checks(results, {'neighbors': []})['reason'], 'inconclusive')
        results['random'] = [{**gate, 'hourly': [0.] * 168} for _ in range(20)]
        self.assertTrue(point_checks(results, {'neighbors': []})['pass'])

    def test_paired_bootstrap_and_degenerate_selector(self):
        columns = np.column_stack((np.ones(48), np.full(48, 2.), np.full(48, 3.)))
        random = np.full((20, 48), -1.)
        draws = bootstrap(columns, random, 24, 100, np.random.default_rng(42))
        np.testing.assert_allclose(draws, np.tile([1., 2., 3.], (100, 1)))
        with self.assertRaisesRegex(ValueError, 'degenerate'):
            select_block(columns)

    def test_no_active_config_from_unfitted_or_failed_result(self):
        result = self.root / 'result.json'
        result.write_text('{"status":"inconclusive"}')
        here = Path(__file__).parent
        with self.assertRaisesRegex(ValueError, 'paper_eligible'):
            export_config(result, here / 'btcusdt.toml', here / 'retention_protocol.json', self.root / 'active.toml')

    def test_statistical_plan_freezes_blocks_and_future_duration(self):
        rng = np.random.default_rng(301)
        baseline = rng.normal(-1., .2, 168)
        gate = rng.normal(1., .2, 168)
        controls = rng.normal(-.5, .2, (20, 168))
        result = plan_test(gate, baseline, controls, [123, 456])
        self.assertGreaterEqual(result['test_days'], 14)
        self.assertGreaterEqual(result['block_hours'], 24)
        test_baseline = rng.normal(-1., .2, 336)
        test_gate = rng.normal(1., .2, 336)
        test_controls = rng.normal(-.5, .2, (20, 336))
        report = test_intervals(test_gate, test_baseline, test_controls, result['block_hours'], [789, 1011])
        self.assertEqual(report['status'], 'paper_eligible')

    def test_funding_failures_are_not_zero_funding(self):
        with self.assertRaisesRegex(ValueError, 'missing settled'):
            funding(0, DAY_NS, self.root / 'funding.json', 2 * DAY_NS, lambda _: [])


if __name__ == '__main__':
    unittest.main()
