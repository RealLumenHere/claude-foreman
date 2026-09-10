#!/usr/bin/env python3
"""burn_rate.py — wq#1669 burn-rate spawn controller.

The old governor-tight clamp was binary and had the sign backwards near a reset:
`tight` clamped every pass to tight_mode.max_spawns_per_pass (1, then 4) exactly
when a pool at 83% used with 4h left to its reset was not scarce but EXPIRING
(research/DISPATCH_GOVERNOR_WALKTHROUGH.md sec 5, 2026-09-10). This module
replaces that clamp with a rate: how many workers can run concurrently and still
land the remaining budget on the reset, per pool, min over pools.

    spend_rate (%/h)   = remaining_pct / hours_to_reset
    allowed_concurrent = clamp(min over pools of spend_rate / quota_per_worker_h,
                               min_allowed, cloud_ceiling)
    allowed_this_pass  = max(0, allowed_concurrent - running_cloud_units)

Properties (walkthrough sec 5): 17% weekly left with 4h to reset is ~7x the
steady-state rate, so the cap opens toward the ceiling; 17% left with 100h to go
clamps down to min_allowed; near-empty near-reset still allows min_allowed
because the quota expires anyway.

Pure like pacing.py: no subprocess, no dispatcher state; foreman_dispatcher.py
owns logging and the fallback (no percent/resets_at data -> the old binary
tight clamp).
"""
import json
import os
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
# Optional measured quota-per-worker rates ({pool: pct_per_worker_hour}), written by
# whatever measures _runs deltas; absent/invalid -> config/default estimates below.
OBSERVED_PATH = os.environ.get("FOREMAN_OBSERVED_BURN",
                               os.path.join(DISPATCHER_DIR, "observed_burn.json"))

WINDOW_S = {"session": 5 * 3600, "weekly_all": 7 * 24 * 3600,
            "fable_weekly_scoped": 7 * 24 * 3600}
# Pass-level pools: every cloud model draws on session + weekly_all, so they bound a
# pass-wide ceiling. fable_weekly_scoped limits fable models alone (router rule:
# "fable" in model), so it only ever narrows a fable model's own ceiling.
PASS_POOLS = ("session", "weekly_all")
FABLE_POOL = "fable_weekly_scoped"

# Pool-% one cloud worker burns per hour, until observed_burn.json supplies measured
# rates. wq#1699 measured 2026-09-10 from journal fworker unit worker-hours vs governor
# state.json percent: session 07:40-10:39Z 0->23% over 18.55 worker-h = 1.24;
# weekly_all 07:00-10:39Z 83->89% over 19.48 worker-h = 0.31. Both include Frank's own
# interactive use of the same subscription, so they overstate a worker's share (the
# safe direction). Rounded up. fable_weekly_scoped is NOT measured (no fable-only
# worker-hours split yet) and keeps the wq#1669 estimate. One night of data: the
# observed_burn.json writer replaces these with a rolling measurement.
DEFAULT_AVG_WORKER_PCT_PER_HOUR = {"session": 1.5, "weekly_all": 0.35,
                                   "fable_weekly_scoped": 0.75}
MIN_ALLOWED = 1


def pools_for_model(model):
    """Pools a model draws on, mirroring router.governor_pressure's checks."""
    return PASS_POOLS + ((FABLE_POOL,) if "fable" in (model or "") else ())


def merge_config(block):
    """config.json's optional `burn_rate` block over the defaults. Fail-soft like
    pacing.load_config: a malformed field keeps its default, never fails the pass."""
    out = {"avg_worker_pct_per_hour": dict(DEFAULT_AVG_WORKER_PCT_PER_HOUR),
           "min_allowed": MIN_ALLOWED}
    if not isinstance(block, dict):
        return out
    rates = block.get("avg_worker_pct_per_hour")
    if isinstance(rates, dict):
        for pool, v in rates.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                out["avg_worker_pct_per_hour"][pool] = float(v)
    ma = block.get("min_allowed")
    if isinstance(ma, int) and not isinstance(ma, bool) and ma >= 0:
        out["min_allowed"] = ma
    return out


def load_observed(path=None):
    """Measured {pool: pct_per_worker_hour}, or {} when absent/unusable."""
    path = path or OBSERVED_PATH
    try:
        with open(path) as f:
            obs = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(obs, dict):
        return {}
    return {k: float(v) for k, v in obs.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}


def pool_spend_rate(limits, pool, now):
    """remaining_pct / hours_to_reset for one pool, or None without reliable data
    (missing percent/resets_at, already rolled over, bogus horizon) — never guess."""
    b = (limits or {}).get(pool) or {}
    pct, resets_at = b.get("percent"), b.get("resets_at")
    if pct is None or not resets_at:
        return None
    try:
        reset_dt = datetime.fromisoformat(resets_at)
    except (TypeError, ValueError):
        return None
    remaining_s = (reset_dt - now).total_seconds()
    if remaining_s <= 0 or remaining_s > WINDOW_S[pool]:
        return None
    remaining_pct = max(0.0, 100.0 - float(pct))
    hours = max(remaining_s / 3600.0, 1.0 / 60.0)  # floor at 1 min: no div-by-~0 blowup
    return remaining_pct / hours


def allowed_concurrent(limits, ceiling, cfg, observed=None, now=None, pools=PASS_POOLS):
    """(allowed, detail): concurrent cloud workers the budget supports, min over
    `pools`, clamped to [min_allowed, ceiling]. (None, why) when none of `pools` has
    reliable data — the caller falls back to the binary tight clamp rather than
    trusting a guess."""
    now = now or datetime.now(timezone.utc)
    observed = observed or {}
    rates = cfg["avg_worker_pct_per_hour"]
    best = None
    details = []
    for pool in pools:
        spend = pool_spend_rate(limits, pool, now)
        if spend is None:
            continue
        q = observed.get(pool, rates.get(pool))
        if not isinstance(q, (int, float)) or q <= 0:
            continue
        n = spend / q
        details.append(f"{pool}: {spend:.2f}%/h / {q:.2f}%/worker-h = {n:.1f}")
        best = n if best is None else min(best, n)
    if best is None:
        return None, f"no usable percent+resets_at in {','.join(pools)}"
    allowed = int(min(max(best, cfg["min_allowed"]), ceiling))
    return allowed, "; ".join(details)


def allowed_this_pass(limits, running_cloud, ceiling, cfg, observed=None, now=None,
                      pools=PASS_POOLS):
    """(spawns allowed this pass, detail) — allowed_concurrent minus already-running
    cloud units. (None, why) when the controller has no data. The dispatcher enforces
    allowed_concurrent at stage 9 against live units instead; this stays for callers
    that only have a snapshot."""
    allowed, detail = allowed_concurrent(limits, ceiling, cfg, observed=observed, now=now,
                                         pools=pools)
    if allowed is None:
        return None, detail
    n = max(0, allowed - max(0, running_cloud))
    return n, f"concurrent={allowed} running={running_cloud}; {detail}"
