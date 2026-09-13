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
import sqlite3
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
# wq (Frank 2026-09-11): reserve a slice of each pool for Frank's OWN interactive chat, so
# fleet claude -p workers (which share the SAME Max subscription pool) don't burn his session
# in half the time while he's idle. Workers only spend against (remaining - reserve). Per-pool.
FRANK_RESERVE_PCT = {
    "session": float(os.environ.get("FOREMAN_RESERVE_SESSION", "30")),      # keep 30% of the 5h session for Frank
    "weekly_all": float(os.environ.get("FOREMAN_RESERVE_WEEKLY", "15")),    # keep 15% of the week
    "fable_weekly_scoped": float(os.environ.get("FOREMAN_RESERVE_FABLE", "10")),
}
DEFAULT_AVG_WORKER_PCT_PER_HOUR = {"session": 1.5, "weekly_all": 0.35,
                                   "fable_weekly_scoped": 0.75}
MIN_ALLOWED = 1

# wq#1889 (despag P2-3, wq#1883 C section): FRANK_RESERVE_PCT above is already
# conservative (it's sized to cover Frank's own interactive use even when he's
# idle — :52-57), but it cannot see that he's mid-session in claude.ai on this SAME
# Max pool right now — that blind spot is what drained his interactive session before
# this reserve existed. frank_active() below answers that from `wq`'s own
# agent_sessions table (Frank's claude.ai orchestrator chats register there via
# `wq whoami --register`, wq:1146, and refresh `last_seen` on every subsequent wq
# subcommand, wq:607-614/616). When active, both consumers of pool_spend_rate swap in
# this wider reserve instead.
FRANK_ACTIVE_WINDOW_MIN = int(os.environ.get("FRANK_ACTIVE_WINDOW_MIN", "20"))
FRANK_RESERVE_PCT_ACTIVE_BUMP = float(os.environ.get("FRANK_RESERVE_PCT_ACTIVE_BUMP", "15"))
FRANK_RESERVE_PCT_ACTIVE = {pool: v + FRANK_RESERVE_PCT_ACTIVE_BUMP
                            for pool, v in FRANK_RESERVE_PCT.items()}

WQ_DB_DEFAULT = "/home/lumen/agents/work-queue/queue.db"


def frank_active(now, db_path=None):
    """(active: bool, detail: str) — True when an `orch-*` agent_sessions row (wq:332-335,
    indexed on base_name) has `last_seen` within FRANK_ACTIVE_WINDOW_MIN of `now`. One
    read-only indexed query, no writes, no new timer. `db_path` defaults to the `WQ_DB`
    env var (matching bin/wq's own override) read at CALL time, not import time, so a
    path swapped in after this module loads (tests; a future db migration) takes effect
    with no restart — the same per-call-not-per-import reasoning `pool_spend_rate` below
    already applies to FRANK_RESERVE_PCT (wq#1883 C.3).

    `last_seen` advances when the orchestrator session RUNS a wq command — a proxy for
    "Frank is actively orchestrating", not a measurement of claude.ai token burn (wq#1883
    C.5). A chat that reads and writes for the whole window without touching `wq` reads
    idle here: a false negative. That is safe by construction, not just by luck:
    FRANK_RESERVE_PCT already overstates a worker's fair share to cover Frank's own
    interactive use (:52-57), so a false negative here only returns dispatch to that
    already-conservative floor — never to an unguarded one. The same posture covers a
    read failure (missing db, locked file, corrupt schema): degrade to (False, ...), not
    a fail-closed refusal, because raising the reserve is a refinement on an
    already-safe baseline, not the safety-critical gate admission.py's LIMIT wall is.
    """
    path = db_path or os.environ.get("WQ_DB", WQ_DB_DEFAULT)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT base_name, last_seen FROM agent_sessions WHERE base_name LIKE 'orch-%'"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return False, f"agent_sessions unreadable ({e}) — treating as inactive"
    freshest_name, freshest_idle = None, None
    for base_name, last_seen in rows:
        try:
            seen = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        idle_min = (now - seen).total_seconds() / 60.0
        if freshest_idle is None or idle_min < freshest_idle:
            freshest_name, freshest_idle = base_name, idle_min
    if freshest_idle is None:
        return False, "no orch- session rows in agent_sessions"
    if freshest_idle <= FRANK_ACTIVE_WINDOW_MIN:
        return True, f"{freshest_name} idle={freshest_idle:.1f}m (window={FRANK_ACTIVE_WINDOW_MIN}m)"
    return False, (f"freshest orch- session {freshest_name} idle={freshest_idle:.1f}m "
                   f"> window={FRANK_ACTIVE_WINDOW_MIN}m")


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


def pool_spend_rate(limits, pool, now, active=False):
    """remaining_pct / hours_to_reset for one pool, or None without reliable data
    (missing percent/resets_at, already rolled over, bogus horizon) — never guess.
    `active` (wq#1889's frank_active(now), computed once per allowed_concurrent call so
    every pool it checks agrees on the same reading) swaps in FRANK_RESERVE_PCT_ACTIVE."""
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
    reserve = (FRANK_RESERVE_PCT_ACTIVE if active else FRANK_RESERVE_PCT).get(pool, 0.0)
    # spend only against budget ABOVE Frank's reserve: at/below the reserve, rate->0 (workers stop, Frank keeps his headroom)
    remaining_pct = max(0.0, (100.0 - float(pct)) - reserve)
    hours = max(remaining_s / 3600.0, 1.0 / 60.0)  # floor at 1 min: no div-by-~0 blowup
    return remaining_pct / hours


def allowed_concurrent(limits, ceiling, cfg, observed=None, now=None, pools=PASS_POOLS):
    """(allowed, detail): concurrent cloud workers the budget supports, min over
    `pools`, clamped to [min_allowed, ceiling]. (None, why) when none of `pools` has
    reliable data — the caller falls back to the binary tight clamp rather than
    trusting a guess. Reads frank_active(now) once so every pool in this call and the
    caller's own log line agree on the same reading (wq#1889)."""
    now = now or datetime.now(timezone.utc)
    observed = observed or {}
    rates = cfg["avg_worker_pct_per_hour"]
    active, active_detail = frank_active(now)
    best = None
    details = [f"frank_active={active} ({active_detail})"]
    for pool in pools:
        spend = pool_spend_rate(limits, pool, now, active=active)
        if spend is None:
            continue
        q = observed.get(pool, rates.get(pool))
        if not isinstance(q, (int, float)) or q <= 0:
            continue
        n = spend / q
        details.append(f"{pool}: {spend:.2f}%/h / {q:.2f}%/worker-h = {n:.1f}")
        best = n if best is None else min(best, n)
    if best is None:
        return None, f"no usable percent+resets_at in {','.join(pools)}; {details[0]}"
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
