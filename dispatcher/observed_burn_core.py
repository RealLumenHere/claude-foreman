#!/usr/bin/env python3
"""observed_burn_core.py — wq#1722 pure math for the observed_burn.json writer.

burn_rate.py (wq#1699) ships per-pool %/worker-hour DEFAULT_AVG_WORKER_PCT_PER_HOUR
measured from a single night (2026-09-10) and reads a live override from
observed_burn.json when present (burn_rate.load_observed). This module is the pure
half of the daemon that keeps that override current: given a governor percent
sample and how many cloud-worker-hours were spent since the last sample, update a
per-pool EMA of %/worker-hour. No I/O here (mirrors governor_core.py / burn_rate.py's
own split) so the reset-crossing and minimum-sample edge cases can be unit tested
without journalctl, lumen-run, or a live state.json.

observed_burn_writer.py (the I/O half) owns: reading governor state.json, gathering
unit lifetimes from `lumen-run list` + journalctl, resolving ticket -> model, and the
atomic write of observed_burn.json + this module's persisted sampler state.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
from burn_rate import FABLE_POOL, PASS_POOLS  # noqa: E402,F401 (re-exported for callers)
from concurrency import LOCAL_POOL_MODELS  # noqa: E402

# Minimum worker-hours before a window's delta is trusted as a rate (ticket: ">=2
# worker-hours") — below this, noise in the percent reading dominates the estimate.
MIN_WORKER_HOURS = 2.0
# EMA smoothing: weight on the newest sample. Low enough that one noisy window (a
# burst of short-lived workers, a slow percent poll) doesn't swing the rate that
# gates spawn concurrency; high enough that a real regime change (new pricing, a
# heavier default model) shows up within a handful of samples, not dozens.
EMA_ALPHA = 0.3


def _parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def is_local_ticket(model):
    """Same rule as concurrency.is_local_unit, applied to an already-resolved model
    string instead of a live `wq show` lookup — the writer resolves each ticket's
    model once per poll and passes it in here, rather than round-tripping per pool."""
    return bool(model) and any(model.startswith(p) for p in LOCAL_POOL_MODELS)


def is_fable_ticket(model):
    """Mirrors burn_rate.pools_for_model's router rule: "fable" in model."""
    return "fable" in (model or "")


def worker_hours(unit_series, window_start, window_end, pool):
    """Cloud fworker worker-hours for `pool` overlapping [window_start, window_end]
    (epoch seconds). `unit_series`: iterable of {"model": str, "start": epoch,
    "end": epoch}. Local-pool units (concurrency.LOCAL_POOL_MODELS) never count —
    they don't draw on the Max-subscription pools this controller governs.
    fable_weekly_scoped only counts fable-model units (router rule: only fable
    models draw on that pool); the pass pools (session/weekly_all) count every
    non-local unit, fable included, since every cloud model draws on those two."""
    if window_end <= window_start:
        return 0.0
    total = 0.0
    for u in unit_series:
        if is_local_ticket(u.get("model")):
            continue
        if pool == FABLE_POOL and not is_fable_ticket(u.get("model")):
            continue
        start, end = u.get("start"), u.get("end")
        if start is None or end is None:
            continue
        overlap = min(end, window_end) - max(start, window_start)
        if overlap > 0:
            total += overlap / 3600.0
    return total


def ema_update(prev_ema, rate, alpha=EMA_ALPHA):
    """Seed on the first sample, else exponential smoothing."""
    return rate if prev_ema is None else alpha * rate + (1 - alpha) * prev_ema


def update_pool_sample(prev, percent, resets_at, worker_hours_val, now,
                       min_worker_hours=MIN_WORKER_HOURS, alpha=EMA_ALPHA):
    """Advance one pool's persisted sample. Returns (new_sample, event):

    event = "seed"               first sample ever for this pool, no rate yet
           | "reset_crossed"     the PREVIOUS sample's resets_at has passed — the
                                  percent delta would span a reset (usage snaps back
                                  toward 0) and is meaningless as a rate; baseline is
                                  reseeded to the current reading, EMA carried as-is
           | "insufficient_sample"  under min_worker_hours — `prev` is returned
                                  UNCHANGED (not even resampled) so the window this
                                  caller measures worker_hours over keeps growing on
                                  the next poll instead of resetting to a tiny one
           | "no_burn"           adequate worker-hours but percent didn't increase
                                  (noise, or a rare down-tick) — baseline reseeded,
                                  not counted as a rate sample
           | "updated"           percent rose over an adequate, reset-free window —
                                  EMA updated from delta_pct / worker_hours_val
    """
    now_iso = now.isoformat()
    if prev is None:
        return {"percent": percent, "resets_at": resets_at, "sampled_at": now_iso,
                "ema": None}, "seed"

    prev_resets_dt = _parse_iso(prev.get("resets_at"))
    if prev_resets_dt is not None and prev_resets_dt <= now:
        return {"percent": percent, "resets_at": resets_at, "sampled_at": now_iso,
                "ema": prev.get("ema")}, "reset_crossed"

    if worker_hours_val < min_worker_hours:
        return dict(prev), "insufficient_sample"

    delta_pct = percent - prev.get("percent", percent)
    if delta_pct <= 0:
        return {"percent": percent, "resets_at": resets_at, "sampled_at": now_iso,
                "ema": prev.get("ema")}, "no_burn"

    rate = delta_pct / worker_hours_val
    new_ema = ema_update(prev.get("ema"), rate, alpha)
    return {"percent": percent, "resets_at": resets_at, "sampled_at": now_iso,
            "ema": new_ema}, "updated"
