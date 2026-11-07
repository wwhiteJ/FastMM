#!/usr/bin/env python3
"""Publish a fixed-window trial result without changing trading parameters."""
import argparse
import json
from pathlib import Path

from report import atomic_json, fetch_funding, fetch_json, read_rows
from trial_accounting import evaluate_trial

NS_PER_MS = 1_000_000


def publish_trial(manifest_path, output_path):
    manifest = json.loads(manifest_path.read_text())
    rows = read_rows(Path(manifest["rows_file"]))
    arguments = (rows, manifest["session_id"], manifest["window_s"], manifest["duration_s"])
    result = evaluate_trial(*arguments, [])
    funding_error = None
    if "start_ns" in result:
        try:
            history = fetch_funding(result["start_ns"] // NS_PER_MS,
                                    result["as_of_ns"] // NS_PER_MS, fetch_json)
            result = evaluate_trial(*arguments, history)
        except (OSError, ValueError, KeyError) as error:
            funding_error = str(error)
            result.update(net_including_funding_usdt=None, stop_optimizing=False)
    result.update(round=manifest["round"], manifest=str(manifest_path),
                  funding_error=funding_error,
                  funding_status="unverified" if funding_error else "settled_history_estimate")
    atomic_json(output_path, result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    publish_trial(args.manifest, args.out)


if __name__ == "__main__":
    main()
