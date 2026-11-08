#!/usr/bin/env python3
"""Close one registered paper trial and independently reconcile its two simulations."""
import csv
import json
import subprocess
import sys
import time
import tomllib
from decimal import Decimal as D
from pathlib import Path

from retention.artifacts import digest, read_json, write_json
from retention.dataset import inspect_part, journal_parts
from retention.ledger import analyze, risk_ok
from retention_cli import fetch_json, funding

SECOND_NS = 1_000_000_000
TRIAL_NS = 10 * 3600 * SECOND_NS
TAIL_NS = 30 * SECOND_NS
ROLES = ('baseline', 'joint')


def validate_lock(lock, now_ns):
    if lock['end_ns'] - lock['start_ns'] != TRIAL_NS:
        raise ValueError('trial must span exactly ten hours')
    if now_ns < lock['end_ns'] + TAIL_NS:
        raise ValueError('trial interval and tail are not complete')
    if set(lock['configs']) != set(ROLES):
        raise ValueError('expected the registered baseline and joint configurations')


def verify_inputs(lock):
    release = Path(lock['release'])
    build = read_json(release / 'BUILD.json')
    if build['source_commit'] != lock['source_commit']:
        raise ValueError('release commit changed')
    for name, expected in build['files'].items():
        if digest(release / name) != expected:
            raise ValueError('release content changed: ' + name)
    for entry in lock['configs'].values():
        if digest(entry['path']) != entry['sha256']:
            raise ValueError('trial configuration changed')
    first = Path(lock['journal_first'])
    current = max((p for p in first.parent.glob('btc-paper-dryrun-*.fmj')
                   if p.stem.rsplit('-', 1)[-1].isdigit()),
                  key=lambda p: int(p.stem.rsplit('-', 1)[-1]))
    if current != first:
        raise ValueError('collector restarted during the trial')


def close_collector(lock, execute):
    for unit in lock['follower_units']:
        execute(['systemctl', 'stop', unit], check=True)
    try:
        execute(['systemctl', 'stop', 'fastmm-paper-feed.service'], check=True)
    finally:
        # A new collector session cannot append to the just-closed files.
        execute(['systemctl', 'start', 'fastmm-paper-feed.service'], check=True)


def audit_dataset(lock, output):
    previous = clock = None
    files = []
    for path in journal_parts(lock['journal_first']):
        previous, clock, low, high, session = inspect_part(path, previous, clock)
        if session != lock['session_id']:
            raise ValueError('unexpected journal session')
        files.append({'path': str(path), 'sha256': digest(path),
                      'first_engine_ns': low, 'last_engine_ns': high})
    if files[0]['first_engine_ns'] > lock['start_ns'] - 60 * SECOND_NS:
        raise ValueError('missing market warmup')
    if files[-1]['last_engine_ns'] < lock['end_ns'] + TAIL_NS:
        raise ValueError('incomplete market interval')
    manifest = {key: lock[key] for key in ('start_ns', 'end_ns', 'session_id')}
    manifest.update(first=lock['journal_first'], files=files,
                    funding={'path': str(output / 'funding.json')})
    write_json(output / 'dataset_manifest.json', manifest)
    return manifest


def streamed_fills(path, session):
    result = []
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row['session_id'] != session:
                raise ValueError('live simulation contains a different collector session')
            if row['type'] == 'fill':
                result.append((row['ts_ns'], row['side'], row['price_raw'],
                               row['qty_raw'], row['fee_raw']))
    return result


def replayed_fills(path):
    scale = D(100_000_000)
    with path.open() as stream:
        return [(int(r['ts_ns']), 0 if r['side'] == 'B' else 1,
                 int(D(r['price']) * scale), int(D(r['qty']) * scale),
                 int(D(r['fee']) * scale)) for r in csv.DictReader(stream)]


def replay_role(role, lock, manifest, output, execute, funding_verified):
    directory = output / ('replay-' + role)
    directory.mkdir(exist_ok=False)
    config = Path(lock['configs'][role]['path'])
    binary = Path(lock['release']) / 'bin/fastmm-paper-replay'
    with (directory / 'process.log').open('w') as log:
        execute(['nice', '-n', '19', str(binary), manifest['first'], str(config), str(directory)],
                check=True, stdout=log, stderr=subprocess.STDOUT)
    summary = read_json(directory / 'summary.json')
    gross = D(str(summary['realized_pnl'])) + D(str(summary['unrealized_pnl']))
    report = {'summary': summary, 'gross_pnl': str(gross), 'errors': []}
    if funding_verified:
        report.update(analyze(directory, manifest, tomllib.loads(config.read_text())['risk']))
    else:
        report['errors'].append('funding_unavailable')
    live = Path(lock['live_directories'][role])
    report['live_replay_fills_equal'] = (
        streamed_fills(live / 'rows.jsonl', lock['session_id']) ==
        replayed_fills(directory / 'fills.csv'))
    if not report['live_replay_fills_equal']:
        report['errors'].append('live_replay_mismatch')
    health = read_json(live / 'health.json')
    if health['as_of_ns'] < lock['end_ns'] or health['log_dropped']:
        report['errors'].append('live_simulation_incomplete')
    if read_json(directory / 'diagnostics.json')['log_dropped']:
        report['errors'].append('replay_logs_dropped')
    return report


def comparison(results):
    baseline, joint = results['baseline'], results['joint']
    if baseline['errors'] or joint['errors']:
        return {'status': 'invalid_or_incomplete', 'results': results}
    gross_delta = D(joint['gross_pnl']) - D(baseline['gross_pnl'])
    net_delta = D(joint['net']) - D(baseline['net'])
    return {'status': 'completed_ten_hour_comparison', 'results': results,
            'gross_delta_usdt': str(gross_delta), 'net_delta_usdt': str(net_delta),
            'risk_noninferiority': risk_ok(joint, baseline),
            'improved_in_this_sample': gross_delta > 0 and net_delta > 0 and risk_ok(joint, baseline),
            'scope': 'Ten-hour paper trial; fixed engineering parameters; no automatic promotion.'}


def finish(lock_path, expected_hash, execute, now_ns):
    if digest(lock_path) != expected_hash:
        raise ValueError('trial lock changed')
    lock = read_json(lock_path)
    validate_lock(lock, now_ns)
    verify_inputs(lock)
    output = lock_path.parent / 'final'
    output.mkdir(exist_ok=False)
    close_collector(lock, execute)
    manifest = audit_dataset(lock, output)
    funding_verified = True
    try:
        funding(lock['start_ns'], lock['end_ns'], output / 'funding.json', now_ns, fetch_json)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        funding_verified = False
        write_json(output / 'funding_error.json', {'error': str(error)})
    results = {}
    for role in ROLES:
        results[role] = replay_role(role, lock, manifest, output, execute, funding_verified)
        write_json(output / (role + '.json'), results[role])
    report = comparison(results)
    report.update(source_commit=lock['source_commit'], start_ns=lock['start_ns'],
                  end_ns=lock['end_ns'], lock_sha256=expected_hash)
    write_json(output / 'comparison.json', report)
    return report


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: overnight_finish.py LOCK_JSON SHA256')
    lock_path = Path(sys.argv[1]).resolve()
    try:
        print(json.dumps(finish(lock_path, sys.argv[2], subprocess.run, time.time_ns()), indent=2))
    except Exception as error:
        write_json(lock_path.parent / 'finish_error.json',
                   {'status': 'failed', 'error': str(error), 'at_ns': time.time_ns()})
        raise
