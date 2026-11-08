"""Preregistered paired time/seed bootstrap; fills are never independent samples."""
import math
from statistics import NormalDist

import arch
import numpy as np
from arch.bootstrap import optimal_block_length


def checked_series(gate, baseline, random):
    g, b, r = np.asarray(gate), np.asarray(baseline), np.asarray(random)
    if g.ndim != 1 or g.shape != b.shape or r.shape != (20, len(g)):
        raise ValueError("unaligned hourly series or missing random control seeds")
    columns = np.column_stack((g - b, g, g - r.mean(axis=0)))
    if not np.isfinite(columns).all() or not np.isfinite(r).all():
        raise ValueError("nonfinite hourly series")
    return columns, r


def select_block(columns):
    if arch.__version__ != "8.0.0":
        raise ValueError("protocol requires arch==8.0.0")
    if len(columns) < 3 or np.any(np.var(columns, axis=0) == 0):
        raise ValueError("insufficient_data: degenerate pilot series")
    estimates = optimal_block_length(columns)["stationary"].to_numpy()
    if not np.isfinite(estimates).all() or np.any(estimates <= 0):
        raise ValueError("insufficient_data: invalid block estimate")
    return max(24, math.ceil(float(estimates.max())))


def bootstrap(columns, random, block, replications, rng):
    n = len(columns)
    output = np.empty((replications, 3))
    for k in range(replications):
        starts = rng.integers(0, n, n)
        restart = rng.random(n) < 1 / block
        indices = np.empty(n, dtype=np.int64)
        indices[0] = starts[0]
        for j in range(1, n):
            indices[j] = starts[j] if restart[j] else (indices[j - 1] + 1) % n
        output[k] = columns[indices].mean(axis=0)
        seeds = rng.integers(0, len(random), len(random))
        output[k, 2] = columns[indices, 1].mean() - random[np.ix_(seeds, indices)].mean()
    return output


def intervals(columns, random, block, seeds):
    reports, variances = {}, []
    for multiplier, seed in zip((1, 2), seeds):
        draws = bootstrap(columns, random, block * multiplier, 10_000, np.random.Generator(np.random.PCG64(seed)))
        ci = np.quantile(draws, [0.025, 0.975], axis=0).T.tolist()
        reports[str(block * multiplier)] = dict(zip(("delta", "own_net", "vs_random"), ci))
        variances.append(len(columns) * float(draws[:, 0].var(ddof=1)))
    return reports, max(variances)


def plan_test(gate, baseline, random, seeds):
    columns, r = checked_series(gate, baseline, random)
    block = select_block(columns)
    if 2 * block >= len(columns):
        raise ValueError("insufficient_data: pilot shorter than two blocks")
    ci, variance = intervals(columns, r, block, seeds)
    effect = -float(np.mean(baseline))
    if effect <= 0:
        raise ValueError("economic_target_changed")
    normal = NormalDist()
    hours = math.ceil((normal.inv_cdf(.975) + normal.inv_cdf(.8)) ** 2 * variance / effect ** 2)
    return {"block_hours": block, "long_run_variance": variance, "economic_effect": effect,
            "required_hours": hours, "test_days": math.ceil(max(336, hours) / 24),
            "pilot_intervals": ci, "planning_scope": "approximation_not_guaranteed_sufficiency"}


def test_intervals(gate, baseline, random, block, seeds):
    columns, r = checked_series(gate, baseline, random)
    observed_block = select_block(columns)
    if observed_block > 2 * block or 2 * block >= len(columns):
        return {"status": "inconclusive", "observed_block_hours": observed_block}
    ci, _ = intervals(columns, r, block, seeds)
    lower = lambda name: all(interval[name][0] > 0 for interval in ci.values())
    status = "no_improvement" if columns[:, 0].mean() <= 0 else "inconclusive"
    if lower("delta") and lower("vs_random"):
        status = "improved_only"
    if all(lower(name) for name in ("delta", "own_net", "vs_random")):
        status = "paper_eligible"
    return {"status": status, "intervals": ci, "observed_block_hours": observed_block}
