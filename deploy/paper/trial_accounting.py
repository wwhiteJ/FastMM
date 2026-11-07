"""Evaluate one predeclared window of a fresh, flat shadow simulation."""
from decimal import Decimal

from accounting import summarize

NS_PER_SECOND = 1_000_000_000


def evaluate_trial(rows, session_id, window_s, duration_s, history):
    if duration_s <= 0 or window_s <= 0 or duration_s % window_s:
        raise ValueError("trial duration must be a positive multiple of the equity interval")
    selected = [row for row in rows if row["session_id"] == session_id]
    bars = [row for row in selected if row["type"] == "equity"]
    if not bars:
        return {"state": "waiting_for_session", "session_id": session_id}
    interval = window_s * NS_PER_SECOND
    start = bars[0]["ts_ns"] - interval
    end = start + duration_s * NS_PER_SECOND
    observed = [bar for bar in bars if bar["ts_ns"] <= end]
    expected = [start + interval * (index + 1) for index in range(len(observed))]
    if [bar["ts_ns"] for bar in observed] != expected:
        raise ValueError("missing, duplicate or irregular equity bars; trial is not valid")
    complete = observed[-1]["ts_ns"] == end
    if bars[-1]["ts_ns"] > end and not complete:
        raise ValueError("trial end snapshot is missing")
    cutoff = observed[-1]["ts_ns"]
    result = summarize([row for row in selected if row["ts_ns"] <= cutoff], history, "100000")
    result.update(state="complete" if complete else "running", start_ns=start, end_ns=end,
                  duration_s=duration_s, elapsed_s=(cutoff - start) // NS_PER_SECOND,
                  initial_position_btc="0", initial_net_pnl_usdt="0",
                  basis="fresh flat session; exact fixed window; mid-marked; fees and funding estimate",
                  infrastructure_costs_included=False,
                  stop_optimizing=complete and Decimal(result["net_including_funding_usdt"]) > 0)
    return result
