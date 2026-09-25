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
    "poll_backoff_max_s": 600,
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


def poll_backoff_bounds(fail_streak, floors):
    """[lo, hi) poll-interval bounds for the next sleep, given consecutive
    primary-fetch failures. wq#639: the daemon hit a sustained HTTP 429 on
    /api/oauth/usage (11:07-13:41+, 2.5h+) and kept polling at the flat 60-90s
    base cadence the whole time — for a sliding-window rate limit, repeated
    requests during the block plausibly keep re-arming it, turning a
    transient limit into an hours-long one and extending the fail-closed
    (stale) window that governs can-dispatch far past what it should be.
    Doubles the interval per consecutive failure, capped at poll_backoff_max_s
    (default 600s, matching stale_fail_closed_seconds — worst case we still
    get roughly one retry per stale window). fail_streak<=0 (healthy or
    still-CCC-covered) keeps the base cadence unchanged.
    """
    lo = floors.get("poll_interval_min_s", DEFAULT_FLOORS["poll_interval_min_s"])
    hi = floors.get("poll_interval_max_s", DEFAULT_FLOORS["poll_interval_max_s"])
    if fail_streak <= 0:
        return lo, hi
    cap = floors.get("poll_backoff_max_s", DEFAULT_FLOORS["poll_backoff_max_s"])
    mult = 2 ** fail_streak
    return min(lo * mult, cap), min(hi * mult, cap)


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


# T4 (BUILD_PLAN.md §T4, wq#618): the LiteLLM local rung — same trusted-route set as
# orchestrator/judgment.py's LOCAL_POOL_MODELS. Deliberately excludes
# nephthys-qwen3-14b (fallback brain only, not a wq worker per models.yaml).
# local-worker (wq#2565): the LiteLLM group alias is dispatchable, so it must be
# governor-exempt like the family ids it resolves to.
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker")


def is_local_model(model):
    return bool(model) and any(model.startswith(m) for m in LOCAL_POOL_MODELS)


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


def _parse_pt_wallclock(s):
    """Parse a fable_floor_schedule step's `until_pt`: naive 'YYYY-MM-DDTHH:MM' in
    Pacific wall-clock time (same fixed PST offset as PT_OFFSET_HOURS elsewhere in
    this module — no DST handling). Returns None on any non-conforming input."""
    if not isinstance(s, str):
        return None
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def resolve_fable_floor(floors, now_utc):
    """Frank's 2026-08-15 ruling (wq#644): the fable_weekly_scoped reserve steps down
    on a schedule instead of holding one static number. `floors["fable_floor_schedule"]`
    is an optional ordered list of {until_pt, min_remaining_pct} steps in Pacific
    wall-clock time; the first step (in list order) whose until_pt is still in the
    future is active, i.e. "the floor is min_remaining_pct until until_pt". Once every
    step's until_pt has passed, or the key is absent/empty, or ANY step is malformed
    (wrong shape, unparsable until_pt, non-numeric min_remaining_pct) — fail closed to
    the plain static fable_weekly_min_remaining_pct key, same posture as a malformed
    JSON floors file: never partially trust a broken schedule.
    """
    static_floor = floors.get("fable_weekly_min_remaining_pct",
                               DEFAULT_FLOORS["fable_weekly_min_remaining_pct"])
    schedule = floors.get("fable_floor_schedule")
    if not schedule or not isinstance(schedule, list):
        return static_floor
    # naive to match _parse_pt_wallclock's naive strptime result (until_pt carries no
    # tzinfo by construction — it's a plain "PT wall clock" string, not an ISO offset).
    now_pt = (now_utc + _dt.timedelta(hours=PT_OFFSET_HOURS)).replace(tzinfo=None)
    steps = []
    for step in schedule:
        if not isinstance(step, dict):
            return static_floor
        until = _parse_pt_wallclock(step.get("until_pt"))
        pct = step.get("min_remaining_pct")
        if until is None or not isinstance(pct, (int, float)) or isinstance(pct, bool):
            return static_floor
        steps.append((until, pct))
    for until, pct in steps:
        if now_pt < until:
            return pct
    # every step's until_pt has passed (schedule ran its course) — same fallback as
    # missing/malformed, not a silent 0: the operator resets the static key for
    # whatever comes after the scheduled window.
    return static_floor


def applicable_floors(model, floors, now_utc):
    """The floors evaluate_dispatch consults for `model`: session and weekly_all
    always, plus the (scheduled) fable reserve for fable models. wq#1737."""
    out = {
        "session": floors.get("session_min_remaining_pct", DEFAULT_FLOORS["session_min_remaining_pct"]),
        "weekly_all": floors.get("weekly_all_min_remaining_pct", DEFAULT_FLOORS["weekly_all_min_remaining_pct"]),
    }
    if is_fable_model(model):
        out["fable_weekly_scoped"] = resolve_fable_floor(floors, now_utc)
    return out


def floors_zeroed(model, floors, now_utc):
    """True when every floor that applies to `model` is 0 — i.e. the usage reading
    has no authority to deny this model even when fresh (wq#1737)."""
    return all(not f for f in applicable_floors(model, floors, now_utc).values())


def evaluate_dispatch(model, *, limits, floors, now_utc, stop_active, stale,
                       live_blocks=None):
    """Pure decision function. `limits` = parse_limits() output (or None if
    no successful poll yet). `live_blocks` = {pool: resets_at_iso} recorded
    from in-band rejected rate_limit_event frames (report-event), which
    override the poll cadence immediately.

    Gate order: STOP -> local rung exempt -> live_blocks -> stale/missing
    limits -> floors. wq#1737: stale/missing limits fail closed ONLY when some
    floor that applies to the model is non-zero. With every applicable floor
    at 0 (floors.json since 2026-08-23) a fresh reading could never deny, so a
    stale one must not either — it turned a 12h /api/oauth/usage 429 streak
    into a fleet-wide dispatch outage. STOP and live_blocks (in-band rejected
    rate_limit_event, the real exhaustion signal) stay hard gates regardless.

    Returns dict: {allow, local_only, reasons: [...]})
    """
    reasons = []
    live_blocks = live_blocks or {}

    if stop_active:
        return {"allow": False, "local_only": True, "reasons": ["STOP kill-switch active"]}

    # T4: the local rung is governor-exempt by design — it costs zero Anthropic quota,
    # so session/weekly floors and live rate_limit_event blocks (both Anthropic-pool
    # concepts) don't apply to it. STOP above is the only gate local dispatch shares
    # with cloud; a floor breach or in-band rejection on the Anthropic side must never
    # stall a ticket that was never going to touch that quota.
    if is_local_model(model):
        return {"allow": True, "local_only": False, "reasons": ["local rung: governor-exempt"]}

    for pool, resets_at in live_blocks.items():
        rt = _parse_iso(resets_at)
        if rt is not None and rt > now_utc:
            reasons.append(f"live rejected rate_limit_event on {pool}, resets {resets_at}")
            return {"allow": False, "local_only": True, "reasons": reasons}

    applicable = applicable_floors(model, floors, now_utc)
    zeroed = all(not f for f in applicable.values())

    if stale or not limits:
        if zeroed:
            return {"allow": True, "local_only": False,
                    "reasons": ["stale usage, floors zeroed: advisory only"]}
        return {"allow": False, "local_only": True,
                "reasons": ["usage state stale/unavailable — fail-closed to local-rung-only"]}

    # A missing bucket (remaining None) only denies when that pool's floor is > 0:
    # same rule as staleness — the reading has no authority on a zero-floor pool.
    labels = {"session": "session", "weekly_all": "weekly_all",
              "fable_weekly_scoped": "Fable weekly_scoped"}
    for pool, floor in applicable.items():
        remaining = _remaining(limits.get(pool))
        if remaining is None:
            if floor:
                reasons.append(f"{labels[pool]} remaining None < floor {floor}")
        elif remaining < floor:
            kind = "reserve floor" if pool == "fable_weekly_scoped" else "floor"
            reasons.append(f"{labels[pool]} remaining {remaining} < {kind} {floor}")

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


def floor_breaches(limits, floors, now_utc):
    """Which floors are currently breached, for ntfy purposes. Returns a
    sorted list of pool names. `now_utc` picks the active fable_floor_schedule
    step (wq#644) the same way evaluate_dispatch does — a breach here must
    agree with what actually gated dispatch."""
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
    if fable_remaining is not None and fable_remaining < resolve_fable_floor(floors, now_utc):
        breached.append("fable_weekly_scoped")
    return breached
