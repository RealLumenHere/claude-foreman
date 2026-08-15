"""foreman-usaged core logic: pure functions for floor evaluation, limit
parsing, and the LIMIT-vs-CRASH-style classification of a live rate_limit
event. No I/O here so it can be unit tested without a network or filesystem.

Ref: BUILD_PLAN.md §T1, PROBE_RESULTS.md P3a, CLAUDE_CLI_INTERNALS.md §D/§E.
"""
from __future__ import annotations

import datetime as _dt

DEFAULT_FLOORS = {
    "session_min_remaining_pct": 20,
    "weekly_all_min_remaining_pct": 15,
    "fable_weekly_min_remaining_pct": 50,
    "stale_fail_closed_seconds": 600,
    "poll_interval_min_s": 60,
    "poll_interval_max_s": 90,
    "primary_fail_threshold": 2,
    "off_peak_days_pt": [0, 1, 2, 3, 4],
    "off_peak_start_hour_pt": 5,
    "off_peak_end_hour_pt": 11,
}

PT_OFFSET_HOURS = -8  # PST; off-peak window is a coarse flag, DST drift is not gated on


def parse_limits(payload):
    """Reduce the /api/oauth/usage `limits[]` array to the three buckets the
    governor watches: session, weekly_all, fable_weekly_scoped. Returns a
    dict of kind -> {percent, resets_at, severity} (missing buckets omitted).
    """
    out = {}
    for entry in payload.get("limits") or []:
        kind = entry.get("kind")
        if kind == "session":
            out["session"] = _slim(entry)
        elif kind == "weekly_all":
            out["weekly_all"] = _slim(entry)
        elif kind == "weekly_scoped":
            scope = entry.get("scope") or {}
            model = scope.get("model") or {}
            display = (model.get("display_name") or "").lower()
            if "fable" in display:
                out["fable_weekly_scoped"] = _slim(entry)
    return out


def _slim(entry):
    return {
        "percent": entry.get("percent"),
        "resets_at": entry.get("resets_at"),
        "severity": entry.get("severity"),
    }


def is_off_peak(now_utc, floors):
    """Weekday 05:00-11:00 PT is flagged: the 5h-session budget burns faster
    then. Informational only — callers may use it to tighten behavior."""
    pt = now_utc + _dt.timedelta(hours=PT_OFFSET_HOURS)
    if pt.weekday() not in floors.get("off_peak_days_pt", DEFAULT_FLOORS["off_peak_days_pt"]):
        return False
    start = floors.get("off_peak_start_hour_pt", DEFAULT_FLOORS["off_peak_start_hour_pt"])
    end = floors.get("off_peak_end_hour_pt", DEFAULT_FLOORS["off_peak_end_hour_pt"])
    return start <= pt.hour < end


def is_fable_model(model):
    return "fable" in (model or "").lower()


def _remaining(bucket):
    if not bucket or bucket.get("percent") is None:
        return None
    return 100 - bucket["percent"]


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate_dispatch(model, *, limits, floors, now_utc, stop_active, stale,
                       live_blocks=None):
    """Pure decision function. `limits` = parse_limits() output (or None if
    no successful poll yet). `live_blocks` = {pool: resets_at_iso} recorded
    from in-band rejected rate_limit_event frames (report-event), which
    override the poll cadence immediately.

    Returns dict: {allow, local_only, reasons: [...]})
    """
    reasons = []
    live_blocks = live_blocks or {}

    if stop_active:
        return {"allow": False, "local_only": True, "reasons": ["STOP kill-switch active"]}

    for pool, resets_at in live_blocks.items():
        rt = _parse_iso(resets_at)
        if rt is not None and rt > now_utc:
            reasons.append(f"live rejected rate_limit_event on {pool}, resets {resets_at}")
            return {"allow": False, "local_only": True, "reasons": reasons}

    if stale or not limits:
        return {"allow": False, "local_only": True,
                "reasons": ["usage state stale/unavailable — fail-closed to local-rung-only"]}

    session_remaining = _remaining(limits.get("session"))
    weekly_remaining = _remaining(limits.get("weekly_all"))
    session_floor = floors.get("session_min_remaining_pct", DEFAULT_FLOORS["session_min_remaining_pct"])
    weekly_floor = floors.get("weekly_all_min_remaining_pct", DEFAULT_FLOORS["weekly_all_min_remaining_pct"])

    if session_remaining is None or session_remaining < session_floor:
        reasons.append(f"session remaining {session_remaining} < floor {session_floor}")
    if weekly_remaining is None or weekly_remaining < weekly_floor:
        reasons.append(f"weekly_all remaining {weekly_remaining} < floor {weekly_floor}")

    if is_fable_model(model):
        fable_remaining = _remaining(limits.get("fable_weekly_scoped"))
        fable_floor = floors.get("fable_weekly_min_remaining_pct", DEFAULT_FLOORS["fable_weekly_min_remaining_pct"])
        if fable_remaining is None or fable_remaining < fable_floor:
            reasons.append(f"Fable weekly_scoped remaining {fable_remaining} < reserve floor {fable_floor}")

    if reasons:
        return {"allow": False, "local_only": True, "reasons": reasons}
    return {"allow": True, "local_only": False, "reasons": []}


def classify_rate_limit_event(frame):
    """Mirrors CLAUDE_CLI_INTERNALS.md §E's LIMIT-vs-CRASH classifier for a
    single rate_limit_event frame. Returns "LIMIT" or "allowed" (this
    governor never sees CRASH — that's a worker-harness concern; T1 only
    needs to recognize a wall hit to fold it into live_blocks).
    """
    status = frame.get("status")
    if status == "rejected":
        return "LIMIT"
    return "allowed"


def floor_breaches(limits, floors):
    """Which floors are currently breached, for ntfy purposes. Returns a
    sorted list of pool names."""
    breached = []
    session_remaining = _remaining((limits or {}).get("session"))
    weekly_remaining = _remaining((limits or {}).get("weekly_all"))
    fable_remaining = _remaining((limits or {}).get("fable_weekly_scoped"))
    if session_remaining is not None and session_remaining < floors.get(
            "session_min_remaining_pct", DEFAULT_FLOORS["session_min_remaining_pct"]):
        breached.append("session")
    if weekly_remaining is not None and weekly_remaining < floors.get(
            "weekly_all_min_remaining_pct", DEFAULT_FLOORS["weekly_all_min_remaining_pct"]):
        breached.append("weekly_all")
    if fable_remaining is not None and fable_remaining < floors.get(
            "fable_weekly_min_remaining_pct", DEFAULT_FLOORS["fable_weekly_min_remaining_pct"]):
        breached.append("fable_weekly_scoped")
    return breached
