"""Market-independent random pauses learned exclusively from complete training cycles."""
import csv
from pathlib import Path

import numpy as np


def random_schedule(cycles, start, stop, seed):
    if not cycles or any(c < 0 or b <= 0 or s not in (1, 2) for c, s, b in cycles):
        raise ValueError("invalid empirical pause cycles")
    rng = np.random.Generator(np.random.PCG64(seed))
    rows, at = [(start, 0)], start
    while at < stop:
        clear, side, blocked = cycles[int(rng.integers(len(cycles)))]
        at += clear
        if at >= stop:
            break
        # Equal-time side transitions coalesce; the explicit initial clear is preserved.
        if len(rows) > 1 and rows[-1][0] == at:
            rows[-1] = (at, side)
        else:
            rows.append((at, side))
        at += blocked
        if at < stop:
            rows.append((at, 0))
    rows.append((stop, 0))
    return rows


def write_schedule(path, rows):
    path = Path(path)
    if path.exists():
        with path.open() as stream:
            expected = [(int(r["ts_ns"]), int(r["blocked"])) for r in csv.DictReader(stream)]
        if expected != rows:
            raise ValueError("immutable random schedule changed")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("ts_ns", "blocked"))
        writer.writerows(rows)
