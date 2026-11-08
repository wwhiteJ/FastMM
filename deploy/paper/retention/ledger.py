"""Decimal cash ledger; hourly accepted-BBO marks, no trade deletion or daily reset."""
import csv
from decimal import Decimal as D
from pathlib import Path

from .artifacts import HOUR_NS, SCALE, read_json
from .features import log_records


def csv_rows(path):
    with open(path, newline="") as stream:
        yield from csv.DictReader(stream)


def read_fills(path, start, end):
    result = []
    for row in csv_rows(path):
        ts = int(row["ts_ns"])
        if not start <= ts < end:
            raise ValueError("incomplete_exit: fill outside partition")
        if row["inst"] != "0" or row["side"] not in ("B", "S"):
            raise ValueError("unsupported fill instrument/side")
        qty, price = D(row["qty"]), D(row["price"])
        if qty <= 0 or price <= 0:
            raise ValueError("invalid fill price/quantity")
        signed = qty if row["side"] == "B" else -qty
        result.append((ts, signed, -signed * price - D(row["fee"]), row))
    return sorted(result, key=lambda f: f[0])


def funding_events(fills, funding, start, end):
    if funding["start_ns"] > start or funding["end_ns"] < end or not funding["verified"]:
        raise ValueError("unverified funding coverage")
    position, index, payments = D(0), 0, []
    for row in sorted(funding["events"], key=lambda x: x["fundingTime"]):
        at = row["fundingTime"] * 1_000_000
        if not start <= at < end:
            continue
        while index < len(fills) and fills[index][0] < at:
            position += fills[index][1]
            index += 1
        amount = -position * D(row["markPrice"]) * D(row["fundingRate"])
        payments.append((at, D(0), amount, None))
    return payments


def cash_metrics(events, marks, start, end, position_limit):
    position = cash = peak = drawdown = area = worst = episode = D(0)
    last, hourly, errors = start, [D(0)], []
    pending = iter(sorted(events, key=lambda e: (e[0], e[3] is not None)))
    event = next(pending, None)
    for boundary in range(start + HOUR_NS, end + 1, HOUR_NS):
        while event is not None and event[0] < boundary:
            at, qty, amount, _ = event
            area += abs(position) * D(at - last) / D(1_000_000_000)
            old = position
            position += qty
            cash += amount
            episode += amount
            if abs(position) > position_limit:
                errors.append("position_limit_exceeded")
            if old * position < 0:
                opening_cash = amount * abs(position / qty)
                worst, episode = max(worst, -(episode - opening_cash)), opening_cash
            if position == 0:
                worst, episode = max(worst, -episode), D(0)
            last, event = at, next(pending, None)
        mark = marks.get(boundary)
        if mark is None:
            raise ValueError("data_invalid: missing UTC-hour accounting record")
        if position and (not mark or not mark["valid"]):
            raise ValueError("data_invalid: missing valid hourly mark")
        mid = D(mark["bid"] + mark["ask"]) / (2 * SCALE) if mark else D(0)
        equity = cash + position * mid
        peak, drawdown = max(peak, equity), max(drawdown, peak - equity)
        hourly.append(equity)
    area += abs(position) * D(end - last) / D(1_000_000_000)
    if position:
        errors.append("incomplete_exit")
    return {"hourly": [float(b - a) for a, b in zip(hourly, hourly[1:])],
            "net": str(hourly[-1]), "worst_episode_loss": str(worst),
            "position_integral_btc_seconds": str(area), "hourly_drawdown": str(drawdown),
            "errors": sorted(set(errors))}


def sampled_risk(path, payments, start, end, max_loss):
    peak = drawdown = paid = D(0)
    funding = iter(payments)
    next_funding = next(funding, None)
    rows, errors = 0, []
    for bar in csv_rows(path):
        at = int(bar["ts_ns"])
        if not start <= at <= end:
            continue
        while next_funding and next_funding[0] <= at:
            paid += next_funding[2]
            next_funding = next(funding, None)
        equity = D(bar["realized"]) + D(bar["unrealized"]) - D(bar["fees"]) + paid
        if D(bar["position"]) and D(bar["mid"]) <= 0:
            errors.append("invalid_risk_mark")
        peak, drawdown = max(peak, equity), max(drawdown, peak - equity)
        if equity <= -max_loss:
            errors.append("risk_budget_exhausted")
        rows += 1
    if not rows:
        errors.append("missing_equity_bars")
    return drawdown, errors


def analyze(directory, manifest, limits):
    directory = Path(directory)
    start, end = manifest["start_ns"], manifest["end_ns"]
    fills = read_fills(directory / "fills.csv", start, end)
    funding = read_json(manifest["funding"]["path"])
    payments = funding_events(fills, funding, start, end)
    marks = {r["ts"]: r for r in log_records(directory / "decisions.log", "retention_equity")}
    metrics = cash_metrics([*fills, *payments], marks, start, end, D(limits["max_position"]))
    drawdown, errors = sampled_risk(directory / "equity.csv", payments, start, end, D(limits["max_loss"]))
    summary = read_json(directory / "summary.json")
    if summary["kill_reason"] and summary["kill_reason"] != "Requested":
        errors.append("risk_budget_exhausted" if "loss" in summary["kill_reason"].lower() else "engineering_invalid")
    if read_json(directory / "diagnostics.json")["log_dropped"]:
        errors.append("diagnostic_records_dropped")
    metrics["errors"] = sorted(set(metrics["errors"] + errors))
    metrics.update(fills=len(fills),
                   notional=str(sum((abs(q) * D(r["price"]) for _, q, _, r in fills), D(0))),
                   fees=str(sum((D(r["fee"]) for _, _, _, r in fills), D(0))),
                   funding=str(sum((p[2] for p in payments), D(0))),
                   cancel_requests=sum(r["kind"] == "cancel" for r in csv_rows(directory / "orders.csv")),
                   max_drawdown=str(max(drawdown, D(metrics.pop("hourly_drawdown")))),
                   drawdown_resolution="30s engine bars plus UTC-hour accepted-BBO ledger")
    return metrics


def risk_ok(gate, baseline):
    keys = ("max_drawdown", "worst_episode_loss", "position_integral_btc_seconds")
    return not gate["errors"] and not baseline["errors"] and all(D(gate[k]) <= D(baseline[k]) for k in keys)
