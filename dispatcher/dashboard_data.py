#!/usr/bin/env python3
"""dashboard_data.py — read-only dispatcher snapshot (wq#718 AUTO-6).

Pure data-gathering for dashboard.py's HTTP layer, kept separate so the aggregation
logic is unit-testable without a live server. Every function is read-only by
construction: no wq claim/release/block, no file writes, no spawns — the guardrail is
explicit that the dashboard is never a control surface (controls stay in systemd/wq
CLI). Reuses the exact gate logic foreman_dispatcher.py itself uses (nodispatch_reason,
frank_gate_reason, unmet_deps) so "eligible" on the dashboard always means the same
thing dispatch does.

snapshot() sections:
  running        currently-live fworker units (ticket, lane, model, started_at,
                 elapsed_s, projected_end)
  queue_depth    per-lane {open, eligible, blocked} for every allowlisted lane
  failure_rate   rolling failure rate, overall + per-lane, 24h and 7d windows
  circuit_breaker  armed/tripped + the window stats behind that verdict
  stuck_tickets  runaway.stuck_ticket_ids() passthrough
  budget         governor state.json's limits/floor_breaches/stop_active/off_peak
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import foreman_dispatcher as fd  # noqa: E402
import circuit_breaker          # noqa: E402
import runaway                  # noqa: E402
import self_heal                # noqa: E402
import concurrency              # noqa: E402
import fworker                  # noqa: E402

WINDOWS_MIN = {"24h": 24 * 60, "7d": 7 * 24 * 60}


def _parse_wq_ts(v):
    """wq's `claimed_at`/`done_at` columns: naive "YYYY-MM-DD HH:MM:SS", written on a
    UTC box (every other timestamp this fleet writes — state.json, dispatcher.log,
    attempt-N.json — is UTC; wq has no timezone suffix of its own to key off). Returns
    an epoch float, or None for absent/unparseable values."""
    if not v or v == "None":
        return None
    try:
        dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp()


def running_workers(now=None):
    now = now if now is not None else time.time()
    try:
        units = concurrency.list_running_units()
    except concurrency.LumenRunError as e:
        return {"error": str(e), "workers": []}
    workers = []
    for unit in units:
        m = concurrency.TICKET_UNIT_RE.match(unit)
        if not m:
            continue
        tid = m.group("id")
        t = self_heal.wq_show(tid)
        started_at = _parse_wq_ts(t.get("claimed_at"))
        archetype = t.get("archetype") or "standard-engineering"
        wall_clock_s = (fworker.WALL_CLOCK_RCA_S if archetype == "rca-investigation"
                        else fworker.WALL_CLOCK_DEFAULT_S)
        workers.append({
            "ticket": int(tid), "unit": unit, "lane": t.get("lane"),
            "model": t.get("model"), "effort": t.get("effort"),
            "started_at": started_at,
            "elapsed_s": (now - started_at) if started_at else None,
            "projected_end": (started_at + wall_clock_s) if started_at else None,
        })
    return {"workers": workers}


def _lane_ticket_ids(lane, status):
    try:
        p = subprocess.run([fd.WQ_BIN, "list", lane, "--status", status],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    ids = []
    for line in p.stdout.splitlines():
        m = fd.LIST_LINE_RE.match(line)
        if m and m.group(5) == status:
            ids.append(int(m.group(1)))
    return ids


def queue_depth(lanes=None):
    if lanes is None:
        try:
            cfg, _warnings, _sha = fd.load_config()
            lanes = sorted(cfg["lanes"].keys())
        except fd.ConfigError:
            lanes = []
    out = {}
    dep_cache = {}
    for lane in lanes:
        open_ids = _lane_ticket_ids(lane, "open") or []
        blocked_ids = _lane_ticket_ids(lane, "blocked") or []
        eligible = 0
        for tid in open_ids:
            try:
                t = fd.router.wq_show(tid)
            except SystemExit:
                continue
            if fd.nodispatch_reason(t) or fd.frank_gate_reason(t):
                continue
            if fd.unmet_deps(t, dep_cache):
                continue
            eligible += 1
        out[lane] = {"open": len(open_ids), "eligible": eligible, "blocked": len(blocked_ids)}
    return out


def _lane_for_ticket(tid, cache):
    if tid not in cache:
        cache[tid] = self_heal.wq_show(tid).get("lane")
    return cache[tid]


def failure_rate_report(now=None):
    """Broad "did the attempt succeed" rate (circuit_breaker.GENERAL_SUCCESS_CLASSES) —
    NOT the narrower CATASTROPHIC_CLASSES rate the breaker trips on; that one is
    reported separately under the `circuit_breaker` snapshot section."""
    now = now if now is not None else time.time()
    attempts = list(circuit_breaker.iter_attempts())
    lane_cache = {}
    overall = {}
    by_lane = {}
    for label, minutes in WINDOWS_MIN.items():
        since = now - minutes * 60
        window = [(f, c, t) for f, c, t in attempts if since <= f <= now]
        total, failures = len(window), sum(
            1 for _, c, _ in window if c not in circuit_breaker.GENERAL_SUCCESS_CLASSES)
        overall[label] = {"total": total, "failures": failures,
                          "rate": (failures / total) if total else 0.0}
        per_lane = {}
        for finished, classification, tid in window:
            lane = _lane_for_ticket(tid, lane_cache) or "unknown"
            rec = per_lane.setdefault(lane, {"total": 0, "failures": 0})
            rec["total"] += 1
            if classification not in circuit_breaker.GENERAL_SUCCESS_CLASSES:
                rec["failures"] += 1
        for lane, rec in per_lane.items():
            rec["rate"] = rec["failures"] / rec["total"] if rec["total"] else 0.0
        by_lane[label] = per_lane
    return {"overall": overall, "by_lane": by_lane}


def circuit_breaker_state():
    tripped, stats = circuit_breaker.evaluate()
    return {"armed": circuit_breaker.is_armed(), "tripped_now": tripped, **stats}


def stuck_tickets():
    return runaway.stuck_ticket_ids()


def budget_state():
    try:
        with open(fd.GOVERNOR_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"error": f"unreadable {fd.GOVERNOR_STATE}"}
    return {k: st.get(k) for k in
            ("limits", "floor_breaches", "stop_active", "off_peak", "stale",
             "cache_age_s", "updated_at")}


def snapshot(now=None):
    now = now if now is not None else time.time()
    return {
        "generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "running": running_workers(now),
        "queue_depth": queue_depth(),
        "failure_rate": failure_rate_report(now),
        "circuit_breaker": circuit_breaker_state(),
        "stuck_tickets": stuck_tickets(),
        "budget": budget_state(),
    }
