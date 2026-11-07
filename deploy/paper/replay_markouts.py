"""Compare paper replay results with as-of bookTicker markouts from the same closed journal."""
import bisect
import csv
import hashlib
import json
import struct
import sys
from decimal import Decimal
from pathlib import Path

EVENT = struct.Struct('<IBBBBIIQQqqQII')
SCALE = 100_000_000
HORIZONS_MS = (10, 100, 1000)


def events(path):
    with path.open('rb') as stream:
        header = stream.read(256)
        assert header[:4] == b'FMJ1'
        stream.seek(struct.unpack_from('<I', header, 8)[0])
        while block := stream.read(64):
            assert len(block) == 64 and block[:4] == b'FMJB'
            data = stream.read(struct.unpack_from('<I', block, 4)[0])
            offset, count = 0, 0
            while offset < len(data):
                event = EVENT.unpack_from(data, offset)
                assert event[0] >= 64 and event[0] % 64 == 0
                assert offset + event[0] <= len(data)
                yield event, data, offset + 64
                offset += event[0]
                count += 1
            assert count == struct.unpack_from('<I', block, 24)[0]


def fills(directory):
    with (directory / 'fills.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    return [dict(ts=int(r['ts_ns']), sign=1 if r['side'] == 'B' else -1,
                 px=int(Decimal(r['price']) * SCALE), qty=int(Decimal(r['qty']) * SCALE),
                 mid=int(Decimal(r['mid']) * SCALE)) for r in rows]


def sample_tickers(base, targets):
    samples = [None] * len(targets)
    paths = [base] + sorted(base.parent.glob(f'{base.stem}.*.fmj'),
                            key=lambda p: int(p.stem.split('.')[-1]))
    hashes, previous, gaps, end = {}, None, 0, 0
    for path in paths:
        with path.open('rb') as stream:
            hashes[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        for event, data, offset in events(path):
            kind, seq, timestamp = event[1], event[7], event[9]
            gaps += previous is not None and seq != previous + 1
            previous = seq
            end = max(end, timestamp)
            if kind != 3:
                continue
            bid, bid_qty, ask, ask_qty = struct.unpack_from('<qqqq', data, offset)
            if not (0 < bid < ask and bid_qty > 0 and ask_qty > 0):
                continue
            index = bisect.bisect_left(targets, timestamp)
            if index == len(targets):
                continue
            value = (timestamp, seq, bid + ask)
            if samples[index] is None or value[:2] > samples[index][:2]:
                samples[index] = value
    latest = None
    for index, value in enumerate(samples):
        if value is not None and (latest is None or value[:2] > latest[:2]):
            latest = value
        samples[index] = latest
    return dict(zip(targets, samples)), hashes, gaps, end


def markouts(rows, samples, end):
    result = {}
    for horizon in HORIZONS_MS:
        values, ages, notional = [], [], 0
        for fill in rows:
            target = fill['ts'] + horizon * 1_000_000
            sample = samples[target]
            if sample is None or target > end:
                continue
            edge = fill['sign'] * (sample[2] - 2 * fill['px']) * fill['qty']
            values.append(edge / (2 * SCALE * SCALE))
            ages.append((target - sample[0]) / 1_000_000)
            notional += fill['px'] * fill['qty'] / (SCALE * SCALE)
        result[str(horizon)] = dict(count=len(values), negative=sum(v < 0 for v in values),
                                    usdt=sum(values), bps=sum(values) / notional * 10000 if notional else None,
                                    max_market_age_ms=max(ages, default=None))
    return result


def describe(directory, rows, samples, end):
    summary = json.loads((directory / 'summary.json').read_text())
    gross = summary['realized_pnl'] + summary['unrealized_pnl']
    capture = sum(f['sign'] * (f['mid'] - f['px']) * f['qty'] / (SCALE * SCALE) for f in rows)
    keys = ('fills', 'maker_fills', 'taker_fills', 'fees', 'net_pnl', 'final_position',
            'orders', 'cancels', 'rejects', 'risk_rejects', 'inventory_max', 'max_drawdown')
    return dict(**{k: summary[k] for k in keys}, gross_pnl=gross,
                depth_mid_capture=capture, depth_mid_inventory_drift=gross-capture,
                ticker_markouts=markouts(rows, samples, end))


def main():
    base = Path(sys.argv[1])
    directories = [Path(p) for p in sys.argv[2:]]
    by_run = {p: fills(p) for p in directories}
    targets = sorted({f['ts'] + h * 1_000_000 for rows in by_run.values()
                      for f in rows for h in HORIZONS_MS})
    samples, hashes, gaps, end = sample_tickers(base, targets)
    assert gaps == 0, f'{gaps} journal sequence gaps'
    output = dict(journals=hashes, sequence_gaps=gaps,
                  runs={p.name: describe(p, rows, samples, end) for p, rows in by_run.items()})
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
