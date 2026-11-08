"""One independently initialized complete policy replay per experiment cell."""
import subprocess
import tomllib
from pathlib import Path

from .artifacts import digest, identity, override_toml, read_json, write_json
from .ledger import analyze


def strategy_parameters(candidate, manifest, rule):
    values = {"gate_mode": 0 if candidate is None else 2, "gate_rule": rule,
              "gate_forced_mask": 0, "entry_start_ns": manifest["start_ns"],
              "entry_stop_ns": manifest["end_ns"] - 70_000_000_000,
              "gate_trace_start_ns": manifest["start_ns"], "trace_quotes": True}
    if candidate:
        p = candidate["parameters"]
        values.update(gate_window_ms=p["window_ms"], gate_trade_num=p["a"]["numerator"],
                      gate_trade_den=p["a"]["denominator"], gate_book_num=p["b"]["numerator"],
                      gate_book_den=p["b"]["denominator"])
    if rule == 3:
        values["gate_mode"] = 2
    return values


def scenario_config(text, protocol, scenario_id):
    scenarios = protocol["cost_and_fill_scenarios"]
    scenario = next(s for s in scenarios if s["id"] == scenario_id)
    combined = {**scenarios[0], **scenario}
    backtest = {"latency_fixed_us": combined["entry_ms"] * 1000,
                "latency_cancel_us": combined["cancel_ms"] * 1000,
                "latency_ack_us": combined["response_ms"] * 1000,
                "queue_conservatism": combined["queue_conservatism"]}
    fees = {"maker_bps": combined["maker_fee_bps"], "taker_bps": combined["taker_fee_bps"]}
    return override_toml(text, {"backtest": backtest, "venues.binance_usdm.fees": fees}), combined


def assert_effective(actual, scenario):
    expected = {"entry_ns": scenario["entry_ms"] * 1_000_000,
                "cancel_ns": scenario["cancel_ms"] * 1_000_000,
                "response_ns": scenario["response_ms"] * 1_000_000,
                "queue_conservatism_bps": round(scenario["queue_conservatism"] * 10_000),
                "maker_cbps": round(scenario["maker_fee_bps"] * 100),
                "taker_cbps": round(scenario["taker_fee_bps"] * 100),
                "entry_jitter_ns": 1_000_000, "cancel_jitter_ns": 1_000_000,
                "response_jitter_ns": 1_000_000, "recorded_arrival": True}
    for key, value in expected.items():
        if actual.get(key) != value:
            raise ValueError(f"ineffective model override: {key}: {actual.get(key)} != {value}")


def result_metrics(output, manifest, limits):
    summary = read_json(output / "summary.json")
    reason = summary["kill_reason"]
    if reason and reason != "Requested":
        return {"errors": ["risk_budget_exhausted" if "loss" in reason.lower() else "engineering_invalid"],
                "kill_reason": reason, "kill_at_s": summary["kill_at_s"]}
    try:
        return analyze(output, manifest, limits)
    except ValueError as error:
        message = str(error)
        status = next((s for s in ("incomplete_exit", "data_invalid") if message.startswith(s)), "engineering_invalid")
        return {"errors": [status], "reason": message}


class Runner:
    def __init__(self, binary, baseline, protocol_path, build_manifest, output, execute):
        self.binary = str(Path(binary).resolve())
        self.baseline = Path(baseline).read_text()
        self.protocol = read_json(protocol_path)
        if digest(baseline) != self.protocol["baseline"]["config_sha256"]:
            raise ValueError("baseline config hash does not match protocol")
        self.provenance = {"binary_sha256": digest(binary), "protocol_sha256": digest(protocol_path),
                           "baseline_sha256": digest(baseline)}
        build = read_json(build_manifest)
        if build["files"]["bin/fastmm-paper-replay"] != self.provenance["binary_sha256"]:
            raise ValueError("binary does not match committed build manifest")
        here = Path(__file__).resolve().parent.parent
        modules = [*sorted((here / "retention").glob("*.py")), here / "retention_cli.py"]
        sources = {"paper/" + str(p.relative_to(here)): digest(p) for p in modules}
        if any(build["files"].get(name) != value for name, value in sources.items()):
            raise ValueError("offline implementation differs from committed build")
        self.provenance.update(source_commit=build["source_commit"], implementation_sha256=identity(sources))
        self.output, self.execute = Path(output), execute

    def run(self, manifest, candidate, scenario_id, rule, schedule):
        params = strategy_parameters(candidate, manifest, rule)
        return self.execute_config(manifest, params, scenario_id, schedule)

    def observe(self, manifest):
        params = strategy_parameters(None, manifest, 0)
        params["gate_mode"] = 1
        return self.execute_config(manifest, params, "S0", None)

    def execute_config(self, manifest, params, scenario_id, schedule):
        text, scenario = scenario_config(self.baseline, self.protocol, scenario_id)
        text = override_toml(text, {"strategy.params": params})
        request = {**self.provenance, "dataset": identity(manifest), "parameters": params,
                   "scenario": scenario, "schedule": digest(schedule) if schedule else None,
                   "config": text}
        output = self.output / identity(request)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "request.json", request)
        result_path = output / "metrics.json"
        if result_path.exists():
            return {**read_json(result_path), "directory": str(output)}
        config_path = output / "input.toml"
        config_path.write_text(text)
        command = [self.binary, manifest["first"], str(config_path), str(output)]
        if schedule:
            command.append(str(schedule))
        if (output / "decisions.log").exists():
            raise ValueError(f"incomplete replay requires a new output root: {output}")
        with (output / "process.log").open("w") as log:
            self.execute(command, check=True, stdout=log, stderr=subprocess.STDOUT)
        assert_effective(read_json(output / "effective_model.json"), scenario)
        metrics = result_metrics(output, manifest, tomllib.loads(text)["risk"])
        write_json(result_path, metrics)
        return {**metrics, "directory": str(output)}
