#!/usr/bin/env python3
"""foreman-usaged — budget governor daemon (wq#613 / BUILD_PLAN.md §T1).

Polls GET /api/oauth/usage every 60-90s (refresh-aware OAuth bearer),
parses limits[] (session / weekly_all / Fable weekly_scoped), falls back to
CCC after 2 consecutive primary failures, fails closed to local-rung-only
if both are stale >10 min, and exposes a can_dispatch(model) decision that
the worker harness (T2a) queries before spawning a cloud worker.

State: /home/lumen/foreman/state.json (atomic write). Kill-switch:
/home/lumen/foreman/STOP (any content, presence is all that matters).
Live events: /home/lumen/foreman/events.json (report-event subcommand),
for in-band rejected rate_limit_event frames a worker observes — these
override poll cadence immediately per CLAUDE_CLI_INTERNALS.md §D.

Usage:
  foreman_usaged.py run [--once]        # poll loop (or single poll + exit)
  foreman_usaged.py can-dispatch MODEL  # exit 0 allow / 1 deny; prints JSON
  foreman_usaged.py status              # print current state.json
  foreman_usaged.py report-event JSON   # record a live rejected event
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import governor_core as core  # noqa: E402

FOREMAN_DIR = os.environ.get("FOREMAN_DIR", "/home/lumen/foreman")
STATE_PATH = os.path.join(FOREMAN_DIR, "state.json")
STOP_PATH = os.path.join(FOREMAN_DIR, "STOP")
EVENTS_PATH = os.path.join(FOREMAN_DIR, "events.json")
FLOORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "floors.json")
CREDS_PATH = os.environ.get("FOREMAN_CREDS_PATH", os.path.expanduser("~/.claude/.credentials.json"))
CLAUDE_BIN = "/home/lumen/.npm-global/bin/claude"
NTFY_SEND = "/home/lumen/bin/ntfy-send"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CCC_ORG_ID = "06598063-72d8-4013-938d-ec6a9213b3e4"
CCC_URL = f"https://claude.ai/api/organizations/{CCC_ORG_ID}/usage"
CCC_COOKIE_FILE = os.environ.get("CCC_SESSION_COOKIE_FILE", "/home/lumen/.secrets/claude-ai-session-cookie")


def log(msg):
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def load_floors():
    floors = dict(core.DEFAULT_FLOORS)
    if os.path.exists(FLOORS_PATH):
        try:
            with open(FLOORS_PATH) as f:
                floors.update(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            log(f"WARN: floors.json unreadable ({e}), using defaults")
    return floors


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def read_bearer():
    with open(CREDS_PATH) as f:
        creds = json.load(f)
    return creds["claudeAiOauth"]["accessToken"]


def refresh_token():
    """Zero-quota refresh trigger: `claude auth status` rewrites
    .credentials.json if the access token is near/past expiry
    (PROBE_RESULTS.md P3a). Never invokes -p (that would spend quota)."""
    try:
        r = subprocess.run([CLAUDE_BIN, "auth", "status"], capture_output=True,
                            timeout=30, env={**os.environ, "HOME": "/home/lumen"})
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError) as e:
        log(f"WARN: refresh_token failed: {e}")
        return False


def fetch_primary():
    """Returns (ok, payload_or_None, err_str_or_None)."""
    for attempt in (1, 2):
        try:
            token = read_bearer()
        except (OSError, KeyError, json.JSONDecodeError) as e:
            return False, None, f"credentials unreadable: {e}"
        req = urllib.request.Request(
            USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return True, json.load(resp), None
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                log("primary 401 — triggering refresh and retrying once")
                refresh_token()
                continue
            return False, None, f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return False, None, str(e)
    return False, None, "401 persisted after refresh"


def fetch_ccc():
    """Best-effort fallback via claude.ai cookie auth (FOREMAN_RESEARCH §6).
    Not currently provisioned with a session cookie on this box — returns
    unavailable rather than guessing. Wire up CCC_SESSION_COOKIE_FILE to
    enable it."""
    if not os.path.exists(CCC_COOKIE_FILE):
        return False, None, f"no CCC session cookie at {CCC_COOKIE_FILE} — fallback unprovisioned"
    try:
        with open(CCC_COOKIE_FILE) as f:
            cookie = f.read().strip()
    except OSError as e:
        return False, None, f"cookie file unreadable: {e}"
    req = urllib.request.Request(CCC_URL, headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, json.load(resp), None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return False, None, str(e)


def load_events():
    if not os.path.exists(EVENTS_PATH):
        return {}
    try:
        with open(EVENTS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def send_ntfy(priority, title, msg):
    delivered = False
    try:
        r = subprocess.run([NTFY_SEND, "-p", priority, "-T", title, msg],
                            capture_output=True, timeout=15, text=True)
        delivered = r.returncode == 0
        if not delivered:
            log(f"WARN: ntfy-send exited {r.returncode}: {r.stderr.strip()}")
    except (subprocess.SubprocessError, OSError) as e:
        log(f"WARN: ntfy-send failed: {e}")
    # wq#1416: journald copy (via log()'s existing print) — ntfy's own cache.db only
    # retains ~12h; vlogs-forwarder already ships this host's journal to bes:9428
    # VictoriaLogs (30d retention).
    log(json.dumps({"event": "ntfy_publish", "title": title, "priority": priority,
                     "delivered": delivered, "msg": msg}))
    return delivered


def poll_once(floors, prev_state):
    now = dt.datetime.now(dt.timezone.utc)
    ok, payload, err = fetch_primary()
    source = "primary"
    is_429 = (not ok) and err == "HTTP 429"
    if not ok:
        log(f"primary fetch failed: {err}")
        if is_429:
            # RELIABILITY_SPEC.md §B: /api/oauth/usage (#31021/#31637) 429s aggressively
            # under polling and may not recover for hours — this is the expected steady
            # state, not an incident. cache_age_s below is what we serve instead.
            log("429 on /api/oauth/usage — reusing cached limits, not treating as an outage")
        fail_streak = (prev_state or {}).get("primary_fail_streak", 0) + 1
        if fail_streak >= floors.get("primary_fail_threshold", 2):
            ok_ccc, payload_ccc, err_ccc = fetch_ccc()
            if ok_ccc:
                ok, payload, source = True, payload_ccc, "ccc"
            else:
                log(f"CCC fallback also failed: {err_ccc}")
    else:
        fail_streak = 0

    last_good_at = (prev_state or {}).get("last_good_at")
    if ok:
        limits = core.parse_limits(payload)
        last_good_at = now.isoformat()
    else:
        limits = (prev_state or {}).get("limits")

    # cache_age_s: how old the limits[] being served are (None until the first-ever good
    # read). A single bad read (429 or otherwise) only ever raises this by one poll
    # interval — `stale` (fail-closed) only trips once cache_age_s exceeds
    # stale_fail_closed_seconds (default 600s), never on one stale read in isolation
    # (RELIABILITY_SPEC.md §B, fworker bug 1: a lone poll failure must not fail-closed).
    cache_age_s = None
    stale = True
    if last_good_at:
        try:
            last_good_dt = dt.datetime.fromisoformat(last_good_at)
        except ValueError:
            last_good_dt = None
        if last_good_dt:
            cache_age_s = (now - last_good_dt).total_seconds()
            stale = cache_age_s > floors.get("stale_fail_closed_seconds", 600)

    stop_active = os.path.exists(STOP_PATH)
    events = load_events()
    live_blocks = {k: v.get("resets_at") for k, v in events.items()}

    breaches = core.floor_breaches(limits, floors, now) if limits else []
    off_peak = core.is_off_peak(now, floors)

    state = {
        "updated_at": now.isoformat(),
        "source": source if ok else (prev_state or {}).get("source", "none"),
        "primary_fail_streak": fail_streak,
        "last_good_at": last_good_at,
        "cache_age_s": cache_age_s,
        "reused_cache": (not ok) and limits is not None,
        "reused_cache_reason": err if (not ok and limits is not None) else None,
        "stale": stale,
        "stop_active": stop_active,
        "off_peak": off_peak,
        "limits": limits,
        "floor_breaches": breaches,
        "live_blocks": live_blocks,
    }
    atomic_write_json(STATE_PATH, state)

    prev_breaches = set((prev_state or {}).get("floor_breaches") or [])
    new_breaches = set(breaches) - prev_breaches
    cleared_breaches = prev_breaches - set(breaches)
    for pool in new_breaches:
        send_ntfy("high", "foreman: reserve floor breached", f"{pool} dropped below its reserve floor")
    for pool in cleared_breaches:
        send_ntfy("default", "foreman: reserve floor recovered", f"{pool} is back above its reserve floor")

    prev_stale = bool((prev_state or {}).get("stale"))
    if stale and not prev_stale:
        send_ntfy("urgent", "foreman: FAIL-CLOSED to local-rung-only",
                   f"usage state stale ({cache_age_s}s since last good poll) — dispatching local rung only")
    elif prev_stale and not stale:
        send_ntfy("default", "foreman: usage polling recovered", "primary/CCC usage poll is fresh again")

    return state


def cmd_run(args):
    floors = load_floors()
    prev_state = load_state()
    if args.once:
        state = poll_once(floors, prev_state)
        print(json.dumps(state, indent=2))
        return 0
    log("foreman-usaged starting poll loop")
    while True:
        try:
            prev_state = poll_once(floors, prev_state)
        except Exception as e:  # noqa: BLE001 — daemon must never die on a bad poll
            log(f"ERROR: poll_once raised {e!r}, continuing")
        fail_streak = (prev_state or {}).get("primary_fail_streak", 0)
        lo, hi = core.poll_backoff_bounds(fail_streak, floors)
        if fail_streak > 0:
            log(f"backing off: fail_streak={fail_streak}, next poll in [{lo:.0f}, {hi:.0f})s")
        time.sleep(random.uniform(lo, hi))


def cmd_can_dispatch(args):
    floors = load_floors()
    state = load_state()
    now = dt.datetime.now(dt.timezone.utc)
    stop_active = os.path.exists(STOP_PATH)
    events = load_events()
    live_blocks = {k: v.get("resets_at") for k, v in events.items()}
    stale = bool((state or {}).get("stale", True))
    limits = (state or {}).get("limits")
    decision = core.evaluate_dispatch(
        args.model, limits=limits, floors=floors, now_utc=now,
        stop_active=stop_active, stale=stale, live_blocks=live_blocks,
    )
    print(json.dumps(decision))
    return 0 if decision["allow"] else 1


def cmd_status(args):
    state = load_state()
    if state is None:
        print(json.dumps({"error": "no state.json yet — daemon has not completed a poll"}))
        return 1
    print(json.dumps(state, indent=2))
    return 0


def cmd_report_event(args):
    try:
        frame = json.loads(args.frame)
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}", file=sys.stderr)
        return 2
    verdict = core.classify_rate_limit_event(frame)
    if verdict != "LIMIT":
        print(json.dumps({"recorded": False, "verdict": verdict}))
        return 0
    pool = frame.get("rateLimitType", "unknown")
    events = load_events()
    events[pool] = {"resets_at": frame.get("resetsAt"), "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    atomic_write_json(EVENTS_PATH, events)
    send_ntfy("high", "foreman: live rejected rate_limit_event", f"pool={pool} resets_at={frame.get('resetsAt')}")
    print(json.dumps({"recorded": True, "pool": pool}))
    return 0


def main():
    p = argparse.ArgumentParser(description="foreman-usaged budget governor")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="poll loop (or single poll with --once)")
    p_run.add_argument("--once", action="store_true")
    p_run.set_defaults(f=cmd_run)

    p_cd = sub.add_parser("can-dispatch", help="decide if MODEL may be dispatched now")
    p_cd.add_argument("model")
    p_cd.set_defaults(f=cmd_can_dispatch)

    p_st = sub.add_parser("status", help="print current state.json")
    p_st.set_defaults(f=cmd_status)

    p_ev = sub.add_parser("report-event", help="record a live rate_limit_event frame (JSON string)")
    p_ev.add_argument("frame")
    p_ev.set_defaults(f=cmd_report_event)

    args = p.parse_args()
    sys.exit(args.f(args))


if __name__ == "__main__":
    main()
