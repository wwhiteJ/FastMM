"""Import causal observations and generate exact empirical-quantile candidates."""
from collections import Counter
from contextlib import closing
from fractions import Fraction
import gzip
import math
import re
import sqlite3

from .artifacts import identity

FIELDS = ("ts", "w_ms", "seq", "bid_q", "ask_q", "buy_v", "sell_v", "valid", "blocked", "resets")


def log_records(path, kind):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as stream:
        for line in stream:
            marker = kind + " "
            if marker not in line:
                continue
            payload = line.split(marker, 1)[1]
            yield {key: (1 if value == "true" else 0 if value == "false" else int(value))
                   for key, value in re.findall(r"(\w+)=(-?\d+|true|false)\b", payload)}


def import_features(database, paths):
    with closing(sqlite3.connect(database)) as db, db:
        columns = ",".join(f"{key} INTEGER NOT NULL" for key in FIELDS)
        db.execute(f"CREATE TABLE IF NOT EXISTS features ({columns}, PRIMARY KEY(ts,w_ms))")
        db.execute("CREATE INDEX IF NOT EXISTS features_window ON features(w_ms,ts)")
        count = 0
        for path in paths:
            for row in log_records(path, "retention"):
                if row.get("inst") != 0:
                    raise ValueError("feature import supports only instrument 0")
                values = tuple(row[key] for key in FIELDS)
                try:
                    db.execute("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?)", values)
                except sqlite3.IntegrityError:
                    old = db.execute("SELECT * FROM features WHERE ts=? AND w_ms=?", values[:2]).fetchone()
                    if old != values:
                        raise ValueError("conflicting observations for one causal snapshot") from None
                count += 1
                if count % 10000 == 0:
                    db.commit()
        return {"rows_read": count, "rows_stored": db.execute("SELECT count(*) FROM features").fetchone()[0]}


def quantiles(counts, ranks):
    total = sum(counts.values())
    if not total:
        raise ValueError("insufficient_signal_support")
    targets = [math.ceil(Fraction(str(rank)) * total) for rank in ranks]
    result, cumulative, index = [], 0, 0
    for value in sorted(counts):
        cumulative += counts[value]
        while index < len(targets) and cumulative >= targets[index]:
            result.append(value)
            index += 1
    return result, total


def window_counts(db, window, start, end, longest):
    trade, book = Counter(), Counter()
    query = """SELECT f.buy_v,f.sell_v,f.bid_q,f.ask_q FROM features f
               JOIN features common ON common.ts=f.ts AND common.w_ms=? AND common.valid=1
               WHERE f.w_ms=? AND f.ts>=? AND f.ts<? AND f.valid=1"""
    for buy, sell, bid, ask in db.execute(query, (longest, window, start, end)):
        if buy != sell and buy + sell > 0:
            trade[Fraction(abs(buy - sell), buy + sell)] += 1
        if bid != ask and bid > 0 and ask > 0:
            book[Fraction(abs(bid - ask), bid + ask)] += 1
    return trade, book


def fraction_json(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def validate_coverage(db, windows, start, end):
    interval = 100_000_000
    expected = (end - start) // interval
    for window in windows:
        count, first, last = db.execute(
            "SELECT count(*),min(ts),max(ts) FROM features WHERE w_ms=? AND ts>=? AND ts<?",
            (window, start, end)).fetchone()
        if count != expected or first >= start + interval or last < end - interval:
            raise ValueError("data_invalid: incomplete equally spaced feature observations")
        gaps = db.execute("""SELECT count(*) FROM (
            SELECT ts-lag(ts) OVER (ORDER BY ts) AS gap FROM features
            WHERE w_ms=? AND ts>=? AND ts<?) WHERE gap != ?""", (window, start, end, interval)).fetchone()[0]
        if gaps:
            raise ValueError("data_invalid: feature snapshot clock changed")


def generate_candidates(database, protocol, start, end):
    search = protocol["candidate_generation"]
    windows = search["window_ms"]
    ranks = search["positive_sample_quantile_ranks"]
    candidates, mapping, support = {}, {}, {}
    with closing(sqlite3.connect(database)) as db:
        validate_coverage(db, windows, start, end)
        for wi, window in enumerate(windows):
            trade_counts, book_counts = window_counts(db, window, start, end, max(windows))
            trade, nt = quantiles(trade_counts, ranks)
            book, nb = quantiles(book_counts, ranks)
            support[str(window)] = {"positive_trade_samples": nt, "positive_book_samples": nb}
            for ai, a in enumerate([Fraction(0), *trade]):
                for bi, b in enumerate([Fraction(0), *book]):
                    parameters = {"window_ms": window, "a": fraction_json(a), "b": fraction_json(b)}
                    key = identity(parameters)
                    item = candidates.setdefault(key, {"id": key, "parameters": parameters, "grid_labels": []})
                    item["grid_labels"].append([wi, ai, bi])
                    mapping[(wi, ai, bi)] = key
    for item in candidates.values():
        neighbors = set()
        for label in item["grid_labels"]:
            for axis in range(3):
                for step in (-1, 1):
                    adjacent = list(label)
                    adjacent[axis] += step
                    neighbor = mapping.get(tuple(adjacent))
                    if neighbor and neighbor != item["id"]:
                        neighbors.add(neighbor)
        item["neighbors"] = sorted(neighbors)
    return {"candidates": sorted(candidates.values(), key=lambda x: x["id"]), "support": support,
            "start_ns": start, "end_ns": end, "status": "research_only"}


def pause_cycles(path, start, stop):
    events = {}
    for row in log_records(path, "retention"):
        if start <= row["ts"] < stop:
            events[row["ts"]] = row
    clear_start, block_start, side, previous_resets = None, None, 0, None
    cycles = []
    clear_duration = 0
    for at, row in sorted(events.items()):
        if not row["valid"] or row["resets"] != previous_resets:
            clear_start = block_start = None
            side = 0
        previous_resets = row["resets"]
        state = row["blocked"] if row["valid"] else 0
        if state == side:
            if state == 0 and row["valid"] and clear_start is None:
                clear_start = at
            continue
        if side and block_start is not None and clear_start is not None:
            cycles.append([clear_duration, side, at - block_start])
        if state:
            clear_duration = at - clear_start if clear_start is not None and side == 0 else 0
            block_start = at
            if side:
                clear_start = at
        else:
            clear_start, block_start = at, None
        side = state
    if not cycles:
        raise ValueError("insufficient_signal_support: no complete pause cycles")
    return cycles
