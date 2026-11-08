"""Auditable train -> validation -> future locked test, with no runner-up retries."""
import csv
import importlib.metadata
import json
import platform
from pathlib import Path

from .artifacts import DAY_NS, digest, identity, read_json, seed, whole_days, write_json
from .controls import random_schedule, write_schedule
from .dataset import verify
from .features import generate_candidates, import_features, pause_cycles
from .selection import choose, point_checks
from .statistics import plan_test, test_intervals


def versions():
    return {"python": platform.python_version(), "packages": {
            d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}


def phase_manifest(path, phase):
    manifest = read_json(path)
    if manifest["phase"] != phase:
        raise ValueError("wrong dataset partition")
    whole_days(manifest["start_ns"], manifest["end_ns"], 7 if phase != "test" else None)
    verify(manifest)
    return manifest


def train(runner, manifest_path, output):
    manifest = phase_manifest(manifest_path, "train")
    output = Path(output)
    provenance = {**runner.provenance, "train_manifest_sha256": digest(manifest_path),
                  "manifest": manifest, "dependencies": versions(),
                  "validation_interval": {"start_ns": manifest["end_ns"], "end_ns": manifest["end_ns"] + 7 * DAY_NS}}
    write_json(output / "train_lock.json", provenance)
    observation = runner.observe(manifest)
    if observation["errors"]:
        raise ValueError("invalid baseline: " + ",".join(observation["errors"]))
    database = output / "features.sqlite"
    import_features(database, [Path(observation["directory"]) / "decisions.log"])
    grid = generate_candidates(database, runner.protocol, manifest["start_ns"], manifest["end_ns"])
    write_json(output / "candidates.json", grid)
    with (output / "candidates.jsonl").open("w") as stream:
        for candidate in grid["candidates"]:
            stream.write(json.dumps(candidate, sort_keys=True) + "\n")
    baseline = runner.run(manifest, None, "S0", 0, None)
    if (baseline["hourly"], baseline["fills"]) != (observation["hourly"], observation["fills"]):
        raise ValueError("engineering_invalid: off/observe mismatch")
    scores = {"B0": baseline}
    for index, candidate in enumerate(grid["candidates"], 1):
        print(f"training {index}/{len(grid['candidates'])}: {candidate['id']}", flush=True)
        scores[candidate["id"]] = runner.run(manifest, candidate, "S0", 0, None)
    verify(manifest)
    result = {**provenance, **choose(grid["candidates"], scores), "runs": scores}
    result["candidate_generation"] = {"sha256": digest(output / "candidates.json"),
                                      "count": len(grid["candidates"]), "support": grid["support"]}
    if "selected" in result:
        directory = scores[result["selected"]["id"]]["directory"]
        result["cycles"] = pause_cycles(Path(directory) / "decisions.log", manifest["start_ns"],
                                       manifest["end_ns"] - 70_000_000_000)
    write_json(output / "selection.json", result)
    write_score_table(output / "train_scores.csv", result.get("scores", []))
    return result


def write_score_table(path, scores):
    with open(path, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("id", "objective", "delta", "eligible"))
        writer.writeheader()
        writer.writerows(scores)


def check_frozen(runner, selection):
    for key, value in runner.provenance.items():
        if selection[key] != value:
            raise ValueError(f"frozen implementation changed: {key}")
    if versions() != selection["dependencies"]:
        raise ValueError("frozen statistical dependencies changed")
    if selection["status"] != "selected_for_validation":
        raise ValueError("training did not select a candidate")


def parameter_provenance(selection):
    selected = selection["selected"]
    params = selected["parameters"]
    return {"w_ns": params["window_ms"] * 1_000_000, "a": params["a"], "b": params["b"],
            "grid_labels": selected["grid_labels"], "candidate_generation": selection["candidate_generation"],
            "training_manifest_sha256": selection["train_manifest_sha256"],
            "winner_score": next(s for s in selection["scores"] if s["id"] == selected["id"]),
            "train_interval": {k: selection["manifest"][k] for k in ("start_ns", "end_ns")},
            "validation_interval": selection["validation_interval"]}


def experiment_seeds(selection, phase, role, count):
    return [seed(selection["protocol_sha256"], selection["train_manifest_sha256"], phase, role, i)
            for i in range(count)]


def evaluation_cells(runner, manifest, selection, candidates, output):
    selected, phase = selection["selected"], manifest["phase"]
    results = {"B0": runner.run(manifest, None, "S0", 0, None),
               "gate": runner.run(manifest, selected, "S0", 0, None),
               "neighbors": {}, "random": [], "ablations": {}, "stress": {}}
    for candidate_id in selected["neighbors"]:
        results["neighbors"][candidate_id] = runner.run(manifest, candidates[candidate_id], "S0", 0, None)
    seeds = experiment_seeds(selection, phase, "random_pause", 20)
    for i, value in enumerate(seeds):
        schedule = Path(output) / f"random-{i:02}.csv"
        write_schedule(schedule, random_schedule(selection["cycles"], manifest["start_ns"],
                                                manifest["end_ns"] - 70_000_000_000, value))
        results["random"].append(runner.run(manifest, selected, "S0", 3, schedule))
    for rule, name in ((1, "trade_only"), (2, "book_only")):
        results["ablations"][name] = runner.run(manifest, selected, "S0", rule, None)
    for scenario in runner.protocol["cost_and_fill_scenarios"][1:]:
        sid = scenario["id"]
        results["stress"][sid] = {"B0": runner.run(manifest, None, sid, 0, None),
                                  "gate": runner.run(manifest, selected, sid, 0, None)}
    return results


def evaluation_input(runner, manifest_path, selection_path, phase):
    selection = read_json(selection_path)
    check_frozen(runner, selection)
    manifest = phase_manifest(manifest_path, phase)
    if phase == "validation" and manifest["start_ns"] != selection["manifest"]["end_ns"]:
        raise ValueError("validation must be the next seven UTC days")
    grid = read_json(Path(selection_path).parent / "candidates.json")
    if digest(Path(selection_path).parent / "candidates.json") != selection["candidate_generation"]["sha256"]:
        raise ValueError("frozen candidate grid changed")
    candidates = {c["id"]: c for c in grid["candidates"]}
    if candidates[selection["selected"]["id"]] != selection["selected"]:
        raise ValueError("selected candidate changed")
    return manifest, selection, candidates


def validate(runner, manifest_path, selection_path, output):
    manifest, selection, candidates = evaluation_input(runner, manifest_path, selection_path, "validation")
    write_json(Path(output) / "validation_lock.json", {"selection_sha256": digest(selection_path),
               "dataset_sha256": digest(manifest_path), "dependencies": versions(),
               "random_seeds": experiment_seeds(selection, "validation", "random_pause", 20),
               "bootstrap_seeds": experiment_seeds(selection, "validation", "bootstrap", 2)})
    results = evaluation_cells(runner, manifest, selection, candidates, output)
    verify(manifest)
    checks = point_checks(results, selection["selected"])
    report = {"status": "validation_failed", "checks": checks, "runs": results,
              "selection_sha256": digest(selection_path), "end_ns": manifest["end_ns"],
              "dataset_manifest_sha256": digest(manifest_path), **runner.provenance}
    if checks["pass"]:
        try:
            report["planning"] = plan_test(results["gate"]["hourly"], results["B0"]["hourly"],
                                           [r["hourly"] for r in results["random"]],
                                           experiment_seeds(selection, "validation", "bootstrap", 2))
            report["status"] = "ready_to_lock_test"
        except ValueError as error:
            report["reason"] = str(error)
    write_json(Path(output) / "validation_report.json", report)
    return report


def lock_test(selection_path, validation_path, output, now_ns):
    selected, report = read_json(selection_path), read_json(validation_path)
    if report["status"] != "ready_to_lock_test" or report["selection_sha256"] != digest(selection_path):
        raise ValueError("validation failed or selection changed")
    start = (max(now_ns, report["end_ns"]) // DAY_NS + 1) * DAY_NS
    lock = {"locked_at_ns": now_ns, "start_ns": start,
            "end_ns": start + report["planning"]["test_days"] * DAY_NS,
            "selection_sha256": digest(selection_path), "validation_sha256": digest(validation_path),
            "planning": report["planning"], "dependencies": versions(),
            "random_seeds": experiment_seeds(selected, "test", "random_pause", 20),
            "bootstrap_seeds": experiment_seeds(selected, "test", "bootstrap", 2)}
    write_json(output, lock)
    return lock


def test(runner, manifest_path, selection_path, lock_path, output):
    manifest, selection, candidates = evaluation_input(runner, manifest_path, selection_path, "test")
    lock = read_json(lock_path)
    if lock["selection_sha256"] != digest(selection_path) or lock["dependencies"] != versions():
        raise ValueError("test lock no longer matches selection/dependencies")
    if (lock["start_ns"], lock["end_ns"]) != (manifest["start_ns"], manifest["end_ns"]):
        raise ValueError("test interval changed: no early peeking or extended endpoint")
    if lock["locked_at_ns"] >= manifest["start_ns"]:
        raise ValueError("test was not preregistered before its start")
    results = evaluation_cells(runner, manifest, selection, candidates, output)
    verify(manifest)
    checks = point_checks(results, selection["selected"])
    report = {"status": checks.get("reason", "inconclusive"), "checks": checks, "runs": results,
              "test_lock_sha256": digest(lock_path), "selected": selection["selected"],
              "selection_sha256": digest(selection_path), "dataset_manifest_sha256": digest(manifest_path),
              "parameter_provenance": parameter_provenance(selection), "planning": lock["planning"],
              "test_interval": {k: lock[k] for k in ("start_ns", "end_ns")}, **runner.provenance}
    if checks["pass"]:
        try:
            report.update(test_intervals(results["gate"]["hourly"], results["B0"]["hourly"],
                                         [r["hourly"] for r in results["random"]],
                                         lock["planning"]["block_hours"], lock["bootstrap_seeds"]))
        except ValueError as error:
            report.update(status="inconclusive", reason=str(error))
    write_json(Path(output) / "test_report.json", report)
    write_json(Path(output) / "calibration_result.json", report)
    return report
