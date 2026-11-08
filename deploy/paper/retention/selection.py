"""One training winner; validation and test never search for a replacement."""
from decimal import Decimal

from .ledger import risk_ok


def delta(gate, baseline):
    if len(gate["hourly"]) != len(baseline["hourly"]):
        raise ValueError("unaligned experiment cells")
    return [g - b for g, b in zip(gate["hourly"], baseline["hourly"])]


def leave_day_out(series):
    if len(series) % 24 or len(series) <= 24:
        raise ValueError("requires complete multiple UTC days")
    total = sum(series)
    return min((total - sum(series[i:i + 24])) / (len(series) - 24)
               for i in range(0, len(series), 24))


def choose(candidates, scores):
    baseline = scores["B0"]
    if baseline["errors"]:
        return {"status": "risk_budget_exhausted" if "risk_budget_exhausted" in baseline["errors"] else "engineering_invalid"}
    ranked, table = [], []
    for candidate in candidates:
        result = scores[candidate["id"]]
        if result["errors"]:
            table.append({"id": candidate["id"], "objective": None, "delta": None, "eligible": False})
            continue
        changes = delta(result, baseline)
        objective = leave_day_out(changes)
        stable = all(not scores[n]["errors"] and sum(delta(scores[n], baseline)) > 0
                     for n in candidate["neighbors"])
        valid = risk_ok(result, baseline) and sum(changes) > 0 and objective > 0 and stable and result["fills"] > 0
        table.append({"id": candidate["id"], "objective": objective, "delta": sum(changes), "eligible": valid})
        if valid:
            ranked.append((-objective, -Decimal(result["notional"]), result["cancel_requests"], candidate["id"], candidate))
    if not ranked:
        return {"status": "no_candidate", "scores": table}
    selected = min(ranked)[-1]
    return {"status": "selected_for_validation", "selected": selected, "scores": table}


def point_checks(results, selected):
    if (len(results["random"]) != 20 or set(results["stress"]) != {"S1", "S2", "S3", "S4", "S5"}
            or set(results["neighbors"]) != set(selected["neighbors"])):
        return {"pass": False, "reason": "engineering_invalid_missing_controls"}
    baseline, gate = results["B0"], results["gate"]
    errors = sorted(set(baseline["errors"] + gate["errors"]))
    if errors:
        return {"pass": False, "reason": errors[0]}
    if not risk_ok(gate, baseline) or gate["fills"] == 0:
        return {"pass": False, "reason": "engineering_invalid"}
    if leave_day_out(delta(gate, baseline)) <= 0:
        return {"pass": False, "reason": "no_improvement"}
    for neighbor in selected["neighbors"]:
        result = results["neighbors"][neighbor]
        if result["errors"] or sum(delta(result, baseline)) <= 0:
            return {"pass": False, "reason": "parameter_unstable"}
    for scenario in results["stress"].values():
        b, g = scenario["B0"], scenario["gate"]
        if not risk_ok(g, b) or sum(delta(g, b)) <= 0 or Decimal(g["net"]) < 0:
            return {"pass": False, "reason": "model_sensitive"}
    if any(r["errors"] for r in results["random"]):
        return {"pass": False, "reason": "invalid_random_control"}
    random_mean = sum(sum(r["hourly"]) for r in results["random"]) / len(results["random"])
    if sum(gate["hourly"]) <= random_mean:
        return {"pass": False, "reason": "inconclusive"}
    return {"pass": True}
