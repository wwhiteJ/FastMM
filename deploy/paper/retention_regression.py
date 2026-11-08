#!/usr/bin/env python3
"""Closed-session engineering checks. Thresholds here are test fixtures, never fitted parameters."""
import csv
import json
import subprocess
import sys
from pathlib import Path

from retention.artifacts import digest, override_toml, write_json
from retention.features import log_records


def run(binary, first, text, root, name, schedule):
    output = root / name
    output.mkdir(parents=True, exist_ok=False)
    config = output / 'input.toml'
    config.write_text(text)
    command = [binary, first, str(config), str(output)]
    if schedule:
        command.append(str(schedule))
    with (output / 'process.log').open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    summary = json.loads((output / 'summary.json').read_text())
    if json.loads((output / 'diagnostics.json').read_text())['log_dropped']:
        raise ValueError('diagnostic loss')
    with (output / 'orders.csv').open() as stream:
        if not list(csv.DictReader(stream)):
            raise ValueError('missing retained order rows')
    print(f'{name}: fills={summary["fills"]}, position={summary["final_position"]}', flush=True)
    return output, summary


def parity(binary, first, config, reference, root):
    off, _ = run(binary, first, config, root, 'off', None)
    observe, _ = run(binary, first, override_toml(config, {'strategy.params': {'gate_mode': 1}}), root, 'observe', None)
    inert_params = {'gate_mode': 2, 'gate_window_ms': 400, 'gate_trade_num': 1,
                   'gate_trade_den': 1, 'gate_book_num': 1, 'gate_book_den': 1}
    inert, _ = run(binary, first, override_toml(config, {'strategy.params': inert_params}), root, 'never-block', None)
    for filename in ('fills.csv', 'equity.csv'):
        if digest(off / filename) != digest(reference / filename) or digest(observe / filename) != digest(off / filename):
            raise ValueError(f'baseline/off/observe mismatch: {filename}')
        if digest(inert / filename) != digest(off / filename):
            raise ValueError(f'active hook changes baseline without any blocked side: {filename}')
    rows = valid = 0
    for row in log_records(observe / 'decisions.log', 'retention'):
        rows += 1
        valid += row['valid']
    if not valid:
        raise ValueError('no valid feature observations')
    return {'rows': rows, 'valid': valid, 'fills_identical': True, 'equity_identical': True,
            'never_block_identical': True}


def scenarios(binary, first, config, root):
    result = {}
    for name, latency, queue, maker, taker in (
            ('S1', 10, 1., 1.8, 4.5), ('S2', 20, 1., 1.8, 4.5),
            ('S3', 50, 1., 1.8, 4.5), ('S4', 5, .5, 1.8, 4.5), ('S5', 5, 1., 2., 5.)):
        text = override_toml(config, {'backtest': {'latency_fixed_us': latency * 1000,
                             'latency_ack_us': latency * 1000, 'latency_cancel_us': latency * 1000,
                             'queue_conservatism': queue},
                             'venues.binance_usdm.fees': {'maker_bps': maker, 'taker_bps': taker}})
        output, _ = run(binary, first, text, root, name, None)
        actual = json.loads((output / 'effective_model.json').read_text())
        expected = {'entry_ns': latency * 1000000, 'cancel_ns': latency * 1000000,
                    'response_ns': latency * 1000000, 'queue_conservatism_bps': round(queue * 10000),
                    'maker_cbps': round(maker * 100), 'taker_cbps': round(taker * 100)}
        if any(actual[key] != value for key, value in expected.items()):
            raise ValueError('ineffective scenario override')
        result[name] = actual
    return result


def active_rules(binary, first, config, reference, root):
    with (reference / 'equity.csv').open() as stream:
        initial = int(next(csv.DictReader(stream))['ts_ns'])
    start, stop = initial + 60_000_000_000, initial + 7200_000_000_000
    params = {'gate_mode': 2, 'gate_window_ms': 400, 'gate_trade_num': 1, 'gate_trade_den': 2,
              'gate_book_num': 1, 'gate_book_den': 2, 'entry_start_ns': start, 'entry_stop_ns': stop}
    schedule = root / 'mask.csv'
    schedule.write_text(f'ts_ns,blocked\n{start},0\n{start},1\n{start+30000000000},2\n{stop},0\n')
    reports = {}
    for rule in range(4):
        text = override_toml(config, {'strategy.params': {**params, 'gate_rule': rule}})
        output, report = run(binary, first, text, root, f'rule-{rule}', schedule if rule == 3 else None)
        if report['final_position'] != 0 or report['inventory_max'] > .006:
            raise ValueError('drain/position regression')
        with (output / 'fills.csv').open() as stream:
            if any(int(row['ts_ns']) < start for row in csv.DictReader(stream)):
                raise ValueError('entry during market-only warmup')
        reports[str(rule)] = {'fills': report['fills'], 'flat': True, 'max_position': report['inventory_max']}
    return reports


def main():
    binary, first, baseline, reference, root = sys.argv[1:]
    reference, root = Path(reference), Path(root)
    root.mkdir(parents=True, exist_ok=False)
    config = Path(baseline).read_text()
    result = {'scope': 'engineering_regression_only_not_calibration', 'binary_sha256': digest(binary)}
    result['parity'] = parity(binary, first, config, reference, root)
    result['scenarios'] = scenarios(binary, first, config, root)
    result['active_rules'] = active_rules(binary, first, config, reference, root)
    write_json(root / 'engineering_report.json', result)


if __name__ == '__main__':
    main()
