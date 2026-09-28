"""wq#1812 despag P1-7 — account-global Max admission gate (PHASE0_WIRING_SPEC.md
RECOMMENDATION step 7 + A.4 rules 3-5).

despag runs `claude -p` turns on the SAME Max subscription pool as Frank's own
interactive chat (A.4 rule 1: the CLI subprocess, fworker's own env, never a
separate API key). This module is the only place a claude-max turn may be
admitted through the gateway. It stacks three gates, cheapest/most-authoritative
first:

  1. LIMIT wall — account-global, in-process (`_paused_resets_at`). Any turn's
     canonical frames showing `saw_limit` (classifier.RunState, the exact fworker
     signal fworker.py:1944 records) pauses EVERY claude-max session, not just the
     one that saw it, until `resets_at` — A.4 rule 3. Checked before the governor
     even runs, since a live rejection is a stronger signal than a stale poll.
  2. foreman-usaged's can-dispatch decision, in-process: `governor_core.
     evaluate_dispatch` fed the exact inputs `foreman_usaged.cmd_can_dispatch`
     (foreman/governor/foreman_usaged.py:364) assembles for the CLI subcommand —
     floors, limits, stale/stop/live_blocks — imported and called directly, never
     shelled out per turn.
  3. `dispatcher/burn_rate.allowed_concurrent` — the FRANK_RESERVE_PCT-aware
     concurrency ceiling (session 30% / weekly 15% / fable 10%, env-overridable;
     burn_rate.py:54-58), clamped to DESPAG_MAX_CLAUDE_CONCURRENT as an upper
     bound only, never a bypass — counted against turns actually in flight in
     THIS process's TurnRegistry, not a config constant. When no pool has usable
     percent+resets_at the ceiling degrades to burn_rate.MIN_ALLOWED, not to
     DESPAG_MAX_CLAUDE_CONCURRENT (wq#1868): with no percent+resets_at there is
     no reserve calculation to be had, so the gate fails closed to one turn the
     way it already does for an unreadable governor read. wq#1889: when
     `burn_rate.frank_active()` sees Frank mid-session on this same Max pool (an
     `orch-*` agent_sessions row, wq#1883 §C.1), `allowed_concurrent` computes the
     ceiling above against the wider FRANK_RESERVE_PCT_ACTIVE instead, internally
     — this module makes no separate check, so it agrees with the dispatcher's
     own ceiling by construction, not by duplicated logic.

litellm-mechanical (the local pool) is exempt from all three gates, mirroring
governor_core.evaluate_dispatch's own local-rung exemption — it costs no
Anthropic quota, so none of this module's state ever touches it.

Fails CLOSED: any exception reading governor state (missing file, corrupt JSON,
foreman_usaged/governor_core raising) is treated as an unreadable/stale read, not
swallowed into an allow.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("despag.admission")

# wq#1991: derived from __file__, never the literal /home/lumen/foreman. A hardcoded
# primary-tree path here put that tree AHEAD of the running worktree's on sys.path at
# import time, so every later bare `import X` in the same process cached the PRIMARY
# copy under that name — wq#1676's test_derive_probe_unit passed alone and failed
# in-suite with `module liveness_clear has no attribute derive_probe`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GOVERNOR_DIR = os.path.join(_REPO_ROOT, "governor")
_DISPATCHER_DIR = os.path.join(_REPO_ROOT, "dispatcher")
_HARNESS_DIR = os.path.join(_REPO_ROOT, "harness")
for _d in (_GOVERNOR_DIR, _DISPATCHER_DIR, _HARNESS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import burn_rate  # noqa: E402 — dispatcher/burn_rate.py, imported not copied (do not edit it)
import classifier  # noqa: E402 — harness/classifier.py's RunState, same LIMIT detector fworker uses
import foreman_usaged  # noqa: E402 — governor/foreman_usaged.py: load_floors/load_state/_daemon_stale,
import governor_core  # noqa: E402 — governor/governor_core.py's evaluate_dispatch (do not edit it)

# fworker.py:148 LOCAL_POOL_MODELS / app.py:57's own duplicate of the same constant —
# despag does not import fworker's router module for this, same reasoning as app.py's
# comment: small and static, not worth a dependency.
# BEGIN GENERATED inference_nodes.yaml v1 — edit the registry, not this block
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker")
# END GENERATED


def is_local_model(model: str | None) -> bool:
    return bool(model) and any(model.startswith(m) for m in LOCAL_POOL_MODELS)


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# fworker.py ~620-645 read_rate_limits_snapshot's own read of the governor's poll
# (state.json, populated by GET /api/oauth/usage) is the "pre-admission usage read"
# this ticket asks for — despag reads the same file rather than re-polling Anthropic
# itself, cached here so N concurrent sessions don't hammer either the file or (if
# foreman-usaged's own cadence ever changed) the upstream endpoint.
DESPAG_USAGE_POLL_SECONDS = _envint("DESPAG_USAGE_POLL_SECONDS", 60)
# Upper clamp only (docstring above) — never treated as a target or a bypass of the
# burn_rate-computed ceiling.
DESPAG_MAX_CLAUDE_CONCURRENT = _envint("DESPAG_MAX_CLAUDE_CONCURRENT", 8)

_state_lock = threading.Lock()
_cached: dict | None = None
_cached_at = 0.0

_pause_lock = threading.Lock()
_paused_resets_at: str | None = None


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_iso(ts: str | None):
    """Same tolerant parse governor_core._parse_iso uses (Z-suffixed or offset ISO),
    kept local so this module never reaches into governor_core's private helpers
    across the process boundary it otherwise treats as a pure function import."""
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_governor_state(force: bool = False) -> dict:
    """Cached `{floors, state}` read of the governor's own floors.json/state.json,
    refreshed at most every DESPAG_USAGE_POLL_SECONDS. Any failure reading either
    file is fail-closed: floors fall back to governor_core.DEFAULT_FLOORS (never
    zero, so a broken read cannot masquerade as the wq#1737 zeroed-floor carve-out)
    and state is treated as absent (stale)."""
    global _cached, _cached_at
    now = time.monotonic()
    with _state_lock:
        if not force and _cached is not None and (now - _cached_at) < DESPAG_USAGE_POLL_SECONDS:
            return _cached
        try:
            floors = foreman_usaged.load_floors()
        except Exception as e:  # noqa: BLE001 — fail closed, never crash admission on a bad read
            logger.warning("admission: floors read failed (%s) — falling back to governor defaults", e)
            floors = dict(governor_core.DEFAULT_FLOORS)
        try:
            state = foreman_usaged.load_state()
        except Exception as e:  # noqa: BLE001
            logger.warning("admission: governor state.json read failed (%s) — treating as stale", e)
            state = None
        _cached = {"floors": floors, "state": state}
        _cached_at = now
        return _cached


def record_limit_frame(resets_at: str | None) -> None:
    """A/4 rule 3: pause the WHOLE claude-max backend, account-global, the instant
    ANY turn's frames show a rejected rate_limit_event — not just the session that
    hit it. `resets_at` of None still pauses (fail closed) but can never un-pause via
    the normal expiry check below, so operators see it via the /v1/uhp budget block
    as `paused_until: null` alongside a permanently-active pause until it's cleared
    by a fresh non-None reading."""
    global _paused_resets_at
    with _pause_lock:
        _paused_resets_at = resets_at
    logger.warning("despag admission: LIMIT wall engaged, claude-max paused until %s", resets_at)


def _limit_wall(now: _dt.datetime) -> dict | None:
    with _pause_lock:
        resets_at = _paused_resets_at
    if resets_at is None:
        return None
    rt = _parse_iso(resets_at)
    if rt is None:
        # Un-boundable reset window (fworker.py:2322-2345 hits the same shape) — fail
        # closed rather than guessing a retry time, but do not spin forever: refuse
        # with no Retry-After, same as quota_exhausted, until a fresh LIMIT frame
        # (or none at all) replaces this reading.
        return {"reasons": [f"account-global LIMIT wall active, unboundable resets_at={resets_at!r}"],
                "retry_after_s": None, "resets_at": resets_at}
    if rt <= now:
        return None  # window has rolled over — treat as cleared, no explicit reset needed
    return {"reasons": [f"account-global LIMIT wall active, resets {resets_at}"],
            "retry_after_s": max(0.0, (rt - now).total_seconds()), "resets_at": resets_at}


class LimitObserver:
    """Feeds one Turn's canonical frames through the same LIMIT detector fworker's
    RunState.observe uses (classifier.py:155), and promotes a detected limit into
    the account-global pause the instant it's seen — not at turn-end — so a second
    in-flight session's very next admission check already sees it."""

    def __init__(self):
        self._state = classifier.RunState()

    @property
    def state(self) -> classifier.RunState:
        """wq#1888 despag P2-2: read-only access to the RunState this observer has
        been accumulating — despag/app.py's terminal-event builder reads it to attach
        the `lumen.run_state` sidecar (PHASE2_WIRING_SPEC.md §B.6), the same signals
        fworker's OWN classifier.RunState computes for a direct claude -p spawn,
        computed here from the identical canonical frames, just never emitted before."""
        return self._state

    def __call__(self, frame: dict) -> None:
        was = self._state.saw_limit
        self._state.observe(frame)
        if self._state.saw_limit and not was:
            record_limit_frame(self._state.latest_resets_at)


def check_admission(model: str, *, running_claude_max: int, now: _dt.datetime | None = None) -> dict:
    """The one gate every claude-max spawn must pass. Returns
    {"allow": bool, "reasons": [...], "retry_after_s": float|None, "resets_at": str|None}.

    `running_claude_max` is the caller's own count of turns in flight for THIS
    process's TurnRegistry (not local-pool) — burn_rate's concurrency ceiling is
    compared against live units, never a config constant (A.4 / RECOMMENDATION step
    7). litellm-mechanical is exempt from every check below, mirroring
    governor_core.evaluate_dispatch's own local-rung exemption.
    """
    now = now or _now()
    if is_local_model(model):
        return {"allow": True, "reasons": ["local rung: governor-exempt"],
                "retry_after_s": None, "resets_at": None}

    wall = _limit_wall(now)
    if wall is not None:
        return {"allow": False, **wall}

    cached = _read_governor_state()
    floors, gstate = cached["floors"], cached["state"]
    try:
        stale = bool((gstate or {}).get("stale", True)) or foreman_usaged._daemon_stale(gstate, floors, now)
        limits = (gstate or {}).get("limits")
        stop_active = os.path.exists(foreman_usaged.STOP_PATH)
        events = foreman_usaged.load_events()
        live_blocks = {k: v.get("resets_at") for k, v in events.items()}
        decision = governor_core.evaluate_dispatch(
            model, limits=limits, floors=floors, now_utc=now,
            stop_active=stop_active, stale=stale, live_blocks=live_blocks,
        )
    except Exception as e:  # noqa: BLE001 — fail closed: a broken governor read must never allow
        logger.warning("admission: governor evaluate_dispatch failed (%s) — refusing claude-max", e)
        return {"allow": False, "reasons": [f"governor state unreadable: {e}"],
                "retry_after_s": None, "resets_at": None}
    if not decision["allow"]:
        return {"allow": False, "reasons": decision["reasons"], "retry_after_s": None, "resets_at": None}

    cfg = burn_rate.merge_config(None)
    observed = burn_rate.load_observed()
    pools = burn_rate.pools_for_model(model)
    allowed, detail = burn_rate.allowed_concurrent(
        limits, DESPAG_MAX_CLAUDE_CONCURRENT, cfg, observed=observed, now=now, pools=pools,
    )
    if allowed is None:
        # allowed_concurrent returns (None, why) when no pool this model draws on has a
        # usable percent+resets_at pair (burn_rate.py:142-143) — a real shape, e.g. a
        # governor poll restart that repopulates percent before resets_at.
        # wq#1868 (wq#1866's verify-pass of wq#1865): substituting
        # DESPAG_MAX_CLAUDE_CONCURRENT here made the clamp the ENTIRE decision, so the
        # same account usage that holds the ceiling to 1 under FRANK_RESERVE_PCT allowed
        # 7 concurrent turns the moment resets_at went missing — the bypass the module
        # docstring forbids. The dispatcher's fallback on this same None
        # (foreman_dispatcher.py:1408-1417) substitutes no concurrency number either: it
        # flips tight mode on, tightening the pass. So fail closed to burn_rate's own
        # floor — one turn — matching the posture the governor-read failure two branches
        # up already uses, still under the operator's upper clamp.
        allowed = min(cfg["min_allowed"], DESPAG_MAX_CLAUDE_CONCURRENT)
        detail = f"{detail}; failing closed to min_allowed={allowed}"
        # wq#2426 made a pool at/below Frank's reserve support ZERO workers, not
        # min_allowed — and that verdict needs only `percent`. So a poll that lost
        # resets_at must not turn a reserve-breached account back into "one turn":
        # that is the wq#1868 loosening again, one slot wide. Percent alone is enough to
        # keep the zero.
        active, _ = burn_rate.frank_active(now)
        breached = [p for p in pools
                    if burn_rate.pool_spendable(limits, p, active=active) == 0.0]
        if breached:
            allowed = 0
            detail = f"{detail}; {','.join(breached)} at/below Frank reserve -> 0"
    if running_claude_max >= allowed:
        return {"allow": False,
                "reasons": [f"burn-rate concurrency ceiling reached: allowed={allowed} "
                            f"running={running_claude_max} ({detail})"],
                "retry_after_s": None, "resets_at": None}

    reasons = list(decision["reasons"])
    if detail:
        reasons.append(detail)
    return {"allow": True, "reasons": reasons, "retry_after_s": None, "resets_at": None}


def budget_snapshot(now: _dt.datetime | None = None) -> dict:
    """The `GET /v1/uhp` `lumen.budget` extension block: session_pct_used,
    weekly_pct_used, reserve_pct (FRANK_RESERVE_PCT in effect), resets_at,
    paused_until, frank_active + frank_active_window_min (wq#1889: whether the
    wider FRANK_RESERVE_PCT_ACTIVE reserve is in effect right now, and the window
    that decision is judged against) — an operator's answer to "why did admission
    refuse"."""
    now = now or _now()
    cached = _read_governor_state()
    limits = (cached["state"] or {}).get("limits") or {}
    session = limits.get("session") or {}
    weekly = limits.get("weekly_all") or {}
    wall = _limit_wall(now)
    active, _detail = burn_rate.frank_active(now)
    return {
        "session_pct_used": session.get("percent"),
        "weekly_pct_used": weekly.get("percent"),
        "reserve_pct": dict(burn_rate.FRANK_RESERVE_PCT),
        "resets_at": session.get("resets_at") or weekly.get("resets_at"),
        "paused_until": wall["resets_at"] if wall is not None else None,
        "frank_active": active,
        "frank_active_window_min": burn_rate.FRANK_ACTIVE_WINDOW_MIN,
    }


def log_startup_reserves() -> None:
    """Log the reserve values in effect at startup (ticket requirement) — read once
    at import/app-startup so an operator can see what was actually loaded, not just
    the code default, without a separate status endpoint. wq#1889: also logs the
    Frank-active reserve and window, so an operator reading startup logs sees both
    the floor and the bump it can jump to, not just the floor — matching what
    budget_snapshot() surfaces at request time."""
    logger.warning(
        "despag admission startup: FRANK_RESERVE_PCT=%s FRANK_RESERVE_PCT_ACTIVE=%s "
        "FRANK_ACTIVE_WINDOW_MIN=%s DESPAG_MAX_CLAUDE_CONCURRENT=%s "
        "DESPAG_USAGE_POLL_SECONDS=%s",
        dict(burn_rate.FRANK_RESERVE_PCT), dict(burn_rate.FRANK_RESERVE_PCT_ACTIVE),
        burn_rate.FRANK_ACTIVE_WINDOW_MIN, DESPAG_MAX_CLAUDE_CONCURRENT, DESPAG_USAGE_POLL_SECONDS,
    )
