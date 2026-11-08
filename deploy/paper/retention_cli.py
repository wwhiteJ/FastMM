#!/usr/bin/env python3
"""Offline calibration driver. The market/decision/execution path is entirely C++."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from retention.artifacts import digest, override_toml, read_json, write_json

HERE = Path(__file__).resolve().parent


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--binary", type=Path)
    result.add_argument("--build-manifest", type=Path)
    result.add_argument("--baseline", type=Path, default=HERE / "btcusdt.toml")
    result.add_argument("--protocol", type=Path, default=HERE / "retention_protocol.json")
    result.add_argument("--work", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    funding = commands.add_parser("funding")
    funding.add_argument("--start-ns", type=int, required=True)
    funding.add_argument("--end-ns", type=int, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--first", type=Path, required=True)
    prepare.add_argument("--start-ns", type=int, required=True)
    prepare.add_argument("--end-ns", type=int, required=True)
    prepare.add_argument("--funding", type=Path, required=True)
    prepare.add_argument("--phase", choices=("train", "validation", "test"), required=True)
    for name in ("train", "validate", "test"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        if name != "train":
            command.add_argument("--selection", type=Path, required=True)
        if name == "test":
            command.add_argument("--lock", type=Path, required=True)
    lock = commands.add_parser("lock-test")
    lock.add_argument("--selection", type=Path, required=True)
    lock.add_argument("--validation", type=Path, required=True)
    export = commands.add_parser("export-config")
    export.add_argument("--result", type=Path, required=True)
    return result


def funding(start, end, output, now_ns, fetch):
    if start >= end or end >= now_ns:
        raise ValueError("funding requires a completed historical interval")
    cursor, events = start // 1_000_000, []
    while cursor < end // 1_000_000:
        query = urlencode({"symbol": "BTCUSDT", "startTime": cursor,
                           "endTime": end // 1_000_000 - 1, "limit": 1000})
        page = fetch("https://fapi.binance.com/fapi/v1/fundingRate?" + query)
        if not isinstance(page, list):
            raise ValueError("funding endpoint returned an error")
        if not page:
            break
        if page[-1]["fundingTime"] < cursor:
            raise ValueError("funding pagination did not advance")
        events.extend(page)
        cursor = page[-1]["fundingTime"] + 1
    if not events:
        raise ValueError("missing settled funding history")
    report = {"start_ns": start, "end_ns": end, "verified": True, "fetched_at_ns": now_ns,
              "source": "https://fapi.binance.com/fapi/v1/fundingRate", "events": events}
    write_json(output, report)
    return report


def export_config(result_path, baseline, protocol_path, output):
    report, protocol = read_json(result_path), read_json(protocol_path)
    if report["status"] != "paper_eligible":
        raise ValueError("only a completed paper_eligible test can export active parameters")
    if digest(baseline) != protocol["baseline"]["config_sha256"] or digest(protocol_path) != report["protocol_sha256"]:
        raise ValueError("promotion provenance mismatch")
    from retention.runner import strategy_parameters
    params = strategy_parameters(report["selected"], {"start_ns": 0, "end_ns": 70_000_000_000}, 0)
    params.update(entry_start_ns=0, entry_stop_ns=0, gate_trace_start_ns=0)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as stream:
        stream.write(override_toml(Path(baseline).read_text(), {"strategy.params": params}))
    write_json(target.with_suffix(".provenance.json"), {"result_sha256": digest(result_path),
               "config_sha256": digest(target), "binary_sha256": report["binary_sha256"]})
    return {"config": str(target), "mode": "paper_active", "result_sha256": digest(result_path)}


def fetch_json(url):
    response = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "30", url],
                              check=True, capture_output=True, text=True)
    return json.loads(response.stdout)


def dispatch(args):
    if args.command == "funding":
        return funding(args.start_ns, args.end_ns, args.work / "funding.json", time.time_ns(), fetch_json)
    if args.command == "prepare":
        from retention.dataset import prepare
        return prepare(args.first, args.work / "dataset", args.start_ns, args.end_ns, args.funding, args.phase)
    if args.command == "export-config":
        return export_config(args.result, args.baseline, args.protocol, args.work / "active.toml")
    from retention import workflow
    if args.command == "lock-test":
        return workflow.lock_test(args.selection, args.validation, args.work / "test_lock.json", time.time_ns())
    from retention.runner import Runner
    if not args.binary:
        raise ValueError("--binary is required for complete C++ policy replays")
    build_manifest = args.build_manifest or args.binary.resolve().parent.parent / "BUILD.json"
    runner = Runner(args.binary, args.baseline, args.protocol, build_manifest, args.work / "runs", subprocess.run)
    if args.command == "train":
        return workflow.train(runner, args.manifest, args.work)
    if args.command == "validate":
        return workflow.validate(runner, args.manifest, args.selection, args.work)
    return workflow.test(runner, args.manifest, args.selection, args.lock, args.work)


if __name__ == "__main__":
    try:
        result = dispatch(parser().parse_args())
        print(json.dumps({key: value for key, value in result.items() if key not in ("runs", "events", "cycles", "scores")}, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
