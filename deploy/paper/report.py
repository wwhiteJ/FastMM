#!/usr/bin/env python3
"""Publish the paper ledger. Network/filesystem/clock live here, outside accounting."""
import argparse
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

from accounting import summarize

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
PAGE_SIZE = 1000
NS_PER_MS = 1_000_000
STALE_SECONDS = 120


def read_rows(path):
    rows = []
    with path.open() as stream:
        for line in stream:
            if not line.endswith("\n"):
                break  # the exporter is still writing this final row
            rows.append(json.loads(line))
    return rows


def fetch_funding(start_ms, end_ms, fetch):
    result = []
    while start_ms <= end_ms:
        query = urllib.parse.urlencode(dict(symbol="BTCUSDT", startTime=start_ms,
                                            endTime=end_ms, limit=PAGE_SIZE))
        page = fetch(FUNDING_URL + "?" + query)
        if not isinstance(page, list):
            raise ValueError("funding endpoint did not return a list")
        result.extend(page)
        if len(page) < PAGE_SIZE:
            break
        next_ms = page[-1]["fundingTime"] + 1
        if next_ms <= start_ms:
            raise ValueError("funding pagination did not advance")
        start_ms = next_ms
    return result


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def publish(directory, now_ns):
    files = sorted(directory.glob("rows-*.jsonl"))
    if not files:
        atomic_json(directory / "summary.json", {"state": "waiting_for_shadow"})
        return
    rows = read_rows(files[-1])
    if not rows:
        atomic_json(directory / "summary.json", {"state": "waiting_for_rows"})
        return
    start_ms = min(row["ts_ns"] for row in rows) // NS_PER_MS
    end_ms = max(row["ts_ns"] for row in rows) // NS_PER_MS
    cache_path = directory / "funding.json"
    history, funding_error = [], None
    try:
        history = fetch_funding(start_ms, end_ms, fetch_json)
        atomic_json(cache_path, history)
    except (OSError, ValueError, KeyError) as error:
        funding_error = str(error)
        if cache_path.exists():
            history = json.loads(cache_path.read_text())
    result = summarize(rows, history, "100000")
    result.update(rows_file=str(files[-1]), published_ns=now_ns, funding_error=funding_error,
                  funding_status="unverified" if funding_error else "settled_history_estimate",
                  pnl_basis="simulation; mid-marked; fees in USDT equivalent; funding external to engine risk",
                  continuity="current collector session; collector restart starts a new flat simulation")
    if "as_of_ns" in result:
        result["age_seconds"] = (now_ns - result["as_of_ns"]) / 1e9
        if result["age_seconds"] > STALE_SECONDS:
            result["state"] = "stale"
    if funding_error:
        result["net_including_funding_usdt"] = None
    atomic_json(directory / "summary.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    publish(args.directory, time.time_ns())


if __name__ == "__main__":
    main()
