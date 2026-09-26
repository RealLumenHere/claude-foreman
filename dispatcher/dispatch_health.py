#!/usr/bin/env python3
"""dispatch_health.py — dispatcher dead-man's switch (wq#825).

PROBLEM (2026-08-22): the dispatcher looped 600+ times over 10h with every pass dying
at stage=config. Night-watch and self_heal.py watch WORKER verdicts, not DISPATCHER
health, so nobody paged Frank.

FIX: this script reads the dispatcher log, classifies the last N passes, and fires an
URGENT ntfy if all N are unhealthy. Runs on its own systemd timer alongside the
dispatcher.

A pass is UNHEALTHY if:
  - It emitted an `error` event (any stage — config error, or any other unhandled
    crash inside the _do_pass loop, wq#1374) and has NO `pass_end` (hard abort).
  - (wq#2088) Its `pass_end` carries per-pool counters and either pool has
    dispatchable_<pool>>0 with spawned_<pool>=0, unless the local lane is full
    (`local_slots_full`), the cloud partition stopped at no_slots, or a partition stopped
    on purpose (kill switch, local pause). dispatchable_<pool> is the READY count (step 8:
    tickets that cleared every gate), so an all-gated queue is healthy by construction.
    Otherwise:
  - Its `pass_end` has spawned=0 AND dispatchable>0 AND dry_run=false AND no
    `halt stage=concurrency reason=no_slots` in the same pass (= the dispatcher COULD
    have spawned but didn't, and it wasn't because all slots were full). This is the
    legacy rule for a `pass_end` that predates the per-pool fields (`dispatchable` was
    then candidates minus a list of structural skip reasons, wq#1410); it falls back to
    raw `candidates` for one that predates `dispatchable` too (pre-migration log tail).
  - It has a `pass_start` with no matching `pass_end` and that `pass_start` is older
    than TIMEOUT_START_SEC (600s, matching the dispatch service's TimeoutStartSec —
    the hung-wq-lock case, wq#1410 §3.3.1 item 1).

A pass is HEALTHY if:
  - It has a `pass_end` with spawned>0, OR
  - It has a `pass_end` with candidates=0 (nothing to do), OR
  - It halted at concurrency/no_slots (ceiling reached, working as designed), OR
  - It was dry_run=true (config_lane soft error, still made decisions).

CLI:
  dispatch_health.py check [--threshold N] [--log PATH] [--dry-run]
  dispatch_health.py inject-fault    # write a bad config for testing
  dispatch_health.py clear-fault     # restore good config
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import vlogs_mirror  # noqa: E402
LOG_PATH = os.environ.get("FOREMAN_DISPATCH_LOG",
                          os.path.join(os.path.dirname(DISPATCHER_DIR), "dispatcher.log"))
STATE_PATH = os.environ.get("FOREMAN_HEALTH_STATE",
                            os.path.join(DISPATCHER_DIR, "runtime", "health_state.json"))
# wq#1253 (#23): health_state.json moved under runtime/ (gitignored) so its own writes
# never dirty a tracked path. One-release fallback for a pre-migration state file still
# sitting at the old tracked location on a host that hasn't run since the move.
_LEGACY_STATE_PATH = os.path.join(DISPATCHER_DIR, "health_state.json")
CONFIG_PATH = os.path.join(DISPATCHER_DIR, "config.json")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")

DEFAULT_THRESHOLD = 2          # consecutive unhealthy passes before alarm (wq#1410:
# K=2 not 3 — at the 20-min health-tick cadence, 3 leaves a one-hour blind spot)
# wq#1411 §4: env/flag-overridable so the Thursday window instance
# (lumen-foreman-health-window.timer) can run K=1 with a 20-min dedup while sharing
# this state file with the regular instance — the tighter page suppresses the 1h one.
ALERT_DEDUP_SECONDS = int(os.environ.get("FOREMAN_HEALTH_ALERT_DEDUP_SECONDS", "3600"))
# How many log lines to scan from the tail. 200 lines covers ~30 passes at ~6 lines each.
TAIL_LINES = 200
# wq#1410: matches dispatcher/systemd/lumen-foreman-dispatch.service TimeoutStartSec —
# a pass_start with no pass_end older than this is the hung-wq-lock case (§1.5).
TIMEOUT_START_SEC = int(os.environ.get("FOREMAN_DISPATCH_TIMEOUT_START_SEC", "600"))


def _utcnow():
    return datetime.now(timezone.utc)


# wq#1410 §3.3.4: the stall page needs to be filterable from the 30-min sentinel
# floor (72 of 159 lumen-alerts messages in one window were priority-5 sentinel
# repeats — a bare "DISPATCHER STALLED" title is indistinguishable from that noise).
# `bin/ntfy-send` (a separate repo, not this branch) only forwards -T/-p today with
# no header passthrough, so "Tags: rotating_light" is a follow-up there; the title
# prefix below is the part achievable from this repo and is what Frank's client can
# match a routing rule against right now.
STALL_TITLE = "[DISPATCH] DISPATCHER STALLED"


def _ntfy(msg, priority="urgent", title=STALL_TITLE):
    vlogs_mirror.mirror("dispatch_health", title, priority, msg)
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def load_state():
    path = STATE_PATH if os.path.exists(STATE_PATH) else _LEGACY_STATE_PATH
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def parse_log_tail(log_path, n_lines):
    """Read last n_lines of the dispatcher log and return parsed JSON events."""
    events = []
    try:
        with open(log_path) as f:
            lines = f.readlines()
        for line in lines[-n_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    except OSError:
        pass
    return events


# A partition that stopped for one of these did so on purpose: spawning nothing is the
# designed answer, not a stall (kill switch, cloud/burn ceiling, local circuit-breaker pause).
PARTITION_STOPS = ("kill_switch", "no_slots", "local_paused")


def _pool_stalls(end, has_no_slots_halt):
    """Stall descriptions for a partitioned pass_end, empty when healthy.

    Step 8 rule: ready_pool > 0 and spawned_pool == 0 and no slots-full / partition-stop
    explanation. `dispatchable_<pool>` is len(ready) - lane_capped, the tickets that cleared
    every gate, so a gate reason nobody listed can never read as a stall. There is no
    `unpooled` branch any more: nothing that failed a gate is counted, and a passing ticket
    always has a pool. Old pass_end lines without per-pool fields never reach this function
    (classify_passes falls back to the top-level `dispatchable`)."""
    stalled = []
    for pool, full in (("local", bool(end.get("local_slots_full"))),
                       ("cloud", has_no_slots_halt)):
        ready = end.get(f"dispatchable_{pool}", 0)
        if ready > 0 and end.get(f"spawned_{pool}", 0) == 0 and not full \
           and end.get(f"stopped_{pool}_reason") not in PARTITION_STOPS:
            stalled.append(f"{pool} spawned=0 dispatchable={ready}")
    return stalled


def classify_passes(events, now=None):
    """Group events by pass_id, classify each pass as healthy/unhealthy.

    Returns list of (pass_id, ts, verdict, reason) in chronological order,
    where verdict is 'healthy', 'unhealthy', or 'skip'.
    """
    now = now or _utcnow()
    passes = OrderedDict()
    for ev in events:
        pid = ev.get("pass_id", "")
        if not pid or not pid.startswith("fdisp-"):
            continue
        passes.setdefault(pid, []).append(ev)

    results = []
    for pid, evs in passes.items():
        ts = evs[0].get("ts", "")

        has_pass_end = False
        has_config_error = False
        is_dry_run = False
        spawned = 0
        candidates = 0
        dispatchable = None
        pool_end = None  # wq#2088: per-pool counters from a partitioned pass_end
        has_no_slots_halt = False
        pass_start_ts = None

        for ev in evs:
            event = ev.get("event", "")
            if event == "error":
                has_config_error = True
            if event == "pass_start":
                pass_start_ts = ev.get("ts")
                if ev.get("dry_run"):
                    is_dry_run = True
            if event == "pass_end":
                has_pass_end = True
                spawned = ev.get("spawned", 0)
                candidates = ev.get("candidates", 0)
                dispatchable = ev.get("dispatchable")
                if "dispatchable_local" in ev:
                    pool_end = ev
            if event == "halt" and ev.get("stage") == "concurrency" \
               and "no_slots" in (ev.get("reason") or ""):
                has_no_slots_halt = True

        # Skip dry-run-only passes (config_lane soft errors; the dispatcher still made
        # decisions, just didn't execute them — that's degraded, not dead).
        if is_dry_run and not has_config_error:
            results.append((pid, ts, "skip", "dry_run pass"))
            continue

        # Case 1: an error event (config error, or any other unhandled crash inside
        # the _do_pass loop — wq#1368 shape) with no following pass_end → dispatcher
        # dead this cycle.
        if has_config_error and not has_pass_end:
            results.append((pid, ts, "unhealthy", "error event (pass aborted)"))
            continue

        # Case 1b (wq#1410 §1.5/§3.3.1): pass_start with no pass_end at all — a hung
        # wq()/sqlite lock with no timeout gives no error line either, just silence
        # until the unit's TimeoutStartSec SIGTERMs it. Only fire once the pass has
        # actually run long enough to be hung, not merely still in flight.
        if not has_pass_end and not has_config_error and pass_start_ts:
            try:
                started = datetime.fromisoformat(pass_start_ts)
                age = (now - started).total_seconds()
            except ValueError:
                age = None
            if age is not None and age > TIMEOUT_START_SEC:
                results.append((pid, ts, "unhealthy",
                                f"hung: pass_start with no pass_end after {int(age)}s"))
                continue

        # Case 2: pass completed but spawned nothing despite dispatchable candidates
        # waiting, and it wasn't because all slots were full. `dispatchable` (wq#1410)
        # excludes structural skips (lane_unmapped etc.); fall back to raw `candidates`
        # for a pass_end predating the field.
        effective_dispatchable = dispatchable if dispatchable is not None else candidates
        # wq#2088: a partitioned pass_end is judged per pool, so a local stall cannot hide
        # behind a healthy cloud (or the reverse). A full local lane is the local analogue
        # of the cloud no_slots stop.
        stalled = _pool_stalls(pool_end, has_no_slots_halt) if pool_end and not is_dry_run \
            else []
        if has_pass_end and stalled:
            results.append((pid, ts, "unhealthy",
                            f"{', '.join(stalled)} candidates={candidates} "
                            f"(not concurrency-limited)"))
            continue
        if has_pass_end and pool_end is None and spawned == 0 and effective_dispatchable > 0 \
           and not has_no_slots_halt and not is_dry_run:
            results.append((pid, ts, "unhealthy",
                            f"spawned=0 dispatchable={effective_dispatchable} "
                            f"candidates={candidates} (not concurrency-limited)"))
            continue

        # Everything else is healthy (or at least not the dispatcher's fault).
        if has_pass_end:
            results.append((pid, ts, "healthy",
                            f"spawned={spawned} candidates={candidates}"))
        elif has_config_error:
            # config error but somehow got a pass_end → shouldn't happen, but be safe
            results.append((pid, ts, "unhealthy", "config error with unexpected pass_end"))
        else:
            # No pass_end, no config error, not yet past timeout — possibly incomplete
            # log line or still in flight, skip
            results.append((pid, ts, "skip", "incomplete pass data"))

    return results


# wq#1694: a stall page must name the cause, not the symptom. On 2026-09-10 the Max
# OAuth session expired; this page said "spawned=0 dispatchable=57" and a human read
# five tools to learn "logged out". stall_cause() names (a) the dominant skip reason
# of the latest scored pass, (b) for governor_deny, the governor's own reason string,
# (c) a dead/expiring Claude session. Everything here is fail-open: a cause lookup
# that errors never suppresses or delays the page itself.
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                              "/home/lumen/foreman/governor/foreman_usaged.py")
CRED_PROBE_DIR = os.environ.get("FOREMAN_CRED_PROBE_DIR", "/home/lumen/sentinel/probes")


def _governor_reason(model):
    try:
        r = subprocess.run([sys.executable, GOVERNOR_CLI, "can-dispatch", model],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout or "{}")
        return "; ".join(d.get("reasons") or []) or ("allow" if d.get("allow") else "deny, no reason given")
    except Exception as e:
        return f"governor query failed: {type(e).__name__}"


def _cred_module():
    """Load credential_expiry from CRED_PROBE_DIR by explicit file path (never via
    sys.path, so a stale path entry cannot substitute another copy)."""
    try:
        import importlib.util
        path = os.path.join(CRED_PROBE_DIR, "credential_expiry.py")
        if not os.path.exists(path):
            return None
        spec = importlib.util.spec_from_file_location("_wq1694_credential_expiry", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def stall_cause(events):
    """Return (cause_text, cred_cause_bool). Never raises."""
    parts, cred = [], False
    try:
        last_pid = None
        for ev in reversed(events):
            if ev.get("event") == "pass_end" and (ev.get("pass_id") or "").startswith("fdisp-"):
                last_pid = ev["pass_id"]
                break
        skips = [ev for ev in events if ev.get("pass_id") == last_pid and ev.get("event") == "skip"]
        counts = {}
        for ev in skips:
            counts[ev.get("reason") or "?"] = counts.get(ev.get("reason") or "?", 0) + 1
        if counts:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
            parts.append("dominant skip: " + ", ".join(f"{k} x{v}" for k, v in top))
        if "governor_deny" in counts:
            models = sorted({ev.get("model") for ev in skips
                             if ev.get("reason") == "governor_deny" and ev.get("model")})
            for m in models[:2]:
                parts.append(f"governor_deny {m}: {_governor_reason(m)}")
    except Exception as e:
        parts.append(f"skip analysis failed: {type(e).__name__}")
    ce = _cred_module()
    if ce is not None:
        line = ce.cause_line()
        if line:
            parts.insert(0, line)
            cred = True
    return " | ".join(parts), cred


def check_health(log_path, threshold, dry_run=False, dedup_seconds=None, ladder=True):
    """Main check. Returns (alarm_fired, detail_string).

    dedup_seconds: override ALERT_DEDUP_SECONDS (wq#1411 window instance: 1200).
    ladder=False: never advance/reset the recovery ladder from this call. The window
    instance runs alongside the regular 5-min one; two callers advancing one rung per
    tick would climb the 3-rung ladder twice as fast and exhaust it (urgent page)
    before a restarted pass could even be scored. The ladder keeps exactly one
    actor — the regular K=2 instance; the window instance only tightens detection.
    """
    dedup_seconds = ALERT_DEDUP_SECONDS if dedup_seconds is None else dedup_seconds
    events = parse_log_tail(log_path, TAIL_LINES)
    if not events:
        return False, "no log data"

    results = classify_passes(events)
    # Filter to only scored passes (healthy/unhealthy), skip dry-run/incomplete.
    scored = [(pid, ts, v, r) for pid, ts, v, r in results if v != "skip"]
    if not scored:
        return False, "no scorable passes in log tail"

    # wq#1410 §3.3.2: a healthy pass resets the recovery ladder immediately, ahead of
    # the alarm window/dedup below — the ladder tracks the dispatcher's own state, not
    # this call's report cadence. Never let a ladder bug break the watchdog itself.
    if scored[-1][2] == "healthy" and ladder:
        try:
            import dispatch_recover
            dispatch_recover.reset(dry_run=dry_run)
        except Exception as e:
            print(f"[dispatch_health] WARN dispatch_recover.reset failed: {e!r}",
                  file=sys.stderr)

    # Take the last `threshold` scored passes.
    recent = scored[-threshold:]
    all_unhealthy = all(v == "unhealthy" for _, _, v, _ in recent)

    detail_lines = []
    for pid, ts, v, r in recent:
        detail_lines.append(f"  {ts} {v}: {r}")

    if not all_unhealthy:
        return False, f"OK — last {len(recent)} passes:\n" + "\n".join(detail_lines)

    # wq#1410 §3.3.2: the alarm condition holds — advance the recovery ladder one
    # rung, every tick, independent of the ntfy dedup below (the ladder must climb
    # all 3 rungs well inside the 1h ntfy dedup window, not wait behind it).
    if ladder:
        try:
            import dispatch_recover
            dispatch_recover.advance(dry_run=dry_run)
        except Exception as e:
            print(f"[dispatch_health] WARN dispatch_recover.advance failed: {e!r}",
                  file=sys.stderr)

    # All recent passes are unhealthy — check dedup.
    state = load_state()
    last_alert = state.get("last_alert_ts")
    now_iso = _utcnow().isoformat(timespec="seconds")
    if last_alert:
        try:
            elapsed = (_utcnow() - datetime.fromisoformat(last_alert)).total_seconds()
            if elapsed < dedup_seconds:
                return False, (f"UNHEALTHY but suppressed (last alert {int(elapsed)}s ago, "
                               f"dedup={dedup_seconds}s):\n" + "\n".join(detail_lines))
        except ValueError:
            pass

    # Fire.
    reasons = "; ".join(r for _, _, _, r in recent)
    cause, cred_cause = stall_cause(events)
    msg = (f"Dispatcher STALLED: last {threshold} passes all unhealthy. "
           f"Backlog not moving.\n{cause + chr(10) if cause else ''}{reasons}")

    if dry_run:
        print(f"WOULD ALERT: {msg}")
        return True, f"ALARM (dry-run):\n" + "\n".join(detail_lines)

    # wq#1694 item 4: when the credential alarm already owns this incident, the stall is
    # a symptom — append it to that aq row instead of opening a second urgent page.
    ce = _cred_module() if cred_cause else None
    if ce is not None and ce.attach_symptom("dispatch-stall", msg.replace("\n", " | ")):
        state["last_alert_ts"] = now_iso
        save_state(state)
        return True, "ALARM ATTACHED to open probe-credential-expiry fire:\n" + "\n".join(detail_lines)

    _ntfy(msg)
    state["last_alert_ts"] = now_iso
    save_state(state)
    return True, f"ALARM FIRED:\n" + "\n".join(detail_lines)


# ---- fault injection for testing (wq#825 acceptance test) ----------------------------

FAULT_KEY = "_" + "_HEALTH_TEST_FAULT__"
FAULT_BAK = CONFIG_PATH + ".bak.healthtest"


def inject_fault():
    """Write a structurally invalid config that will make the dispatcher emit
    error stage=config on its next pass. Backs up the real config first.

    NOTE (wq#827 verify fix, 2026-08-23): the original version added a bad lane
    (__HEALTHTEST__ with max_per_day:-999), but wq#824 made per-lane validation
    SOFT (stage=config_lane, not stage=config), so the health checker correctly
    classified the pass as healthy (it continued and spawned). This version
    corrupts a top-level required field (max_spawns_per_pass) instead, which is
    a hard config error → the pass aborts → dispatch_health.py detects it."""
    with open(CONFIG_PATH) as f:
        real = f.read()
    with open(FAULT_BAK, "w") as f:
        f.write(real)
    cfg = json.loads(real)
    cfg["max_spawns_per_pass"] = "HEALTHTEST_INVALID"
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"FAULT INJECTED: set max_spawns_per_pass to invalid value in {CONFIG_PATH}")
    print(f"Backup: {FAULT_BAK}")
    print("The dispatcher's next pass will emit error stage=config (hard abort).")


def clear_fault():
    """Restore the real config from the fault-injection backup."""
    if not os.path.exists(FAULT_BAK):
        print(f"No fault backup at {FAULT_BAK} — nothing to restore.")
        return
    with open(FAULT_BAK) as f:
        real = f.read()
    with open(CONFIG_PATH, "w") as f:
        f.write(real)
    os.remove(FAULT_BAK)
    print(f"Config restored from {FAULT_BAK}.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Dispatcher dead-man's switch (wq#825)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check dispatcher health and alert if stalled")
    c.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"consecutive unhealthy passes before alarm (default {DEFAULT_THRESHOLD})")
    c.add_argument("--log", default=LOG_PATH, help="dispatcher log path")
    c.add_argument("--dry-run", action="store_true",
                   help="classify + report, don't ntfy")
    c.add_argument("--dedup", type=int, default=None, metavar="SECONDS",
                   help=f"re-alert suppression window (default {ALERT_DEDUP_SECONDS}s; "
                        "wq#1411 Thu window instance uses 1200)")
    c.add_argument("--no-ladder", action="store_true",
                   help="never advance/reset the recovery ladder from this instance "
                        "(wq#1411: the window instance; the regular timer owns the ladder)")

    sub.add_parser("inject-fault", help="write a bad config for acceptance testing")
    sub.add_parser("clear-fault", help="restore config after testing")

    # For unit testing: parse a log snippet and print classifications.
    d = sub.add_parser("classify", help="parse log and print pass classifications")
    d.add_argument("--log", default=LOG_PATH)

    a = ap.parse_args(argv)

    if a.cmd == "check":
        fired, detail = check_health(a.log, a.threshold, dry_run=a.dry_run,
                                     dedup_seconds=a.dedup, ladder=not a.no_ladder)
        print(detail)
        # wq#1012/R2-T5: on the real timer-driven pass the DISPATCHER STALLED alarm is
        # delivered via _ntfy() inside check_health(); a nonzero exit here only made
        # systemd mark lumen-foreman-health failed -> "failed-unit + recovered" echo per
        # stall. Exit 0 on the real pass so the ntfy IS the output. --dry-run keeps the
        # nonzero exit so test/CI callers can still read the fired boolean.
        if a.dry_run:
            sys.exit(2 if fired else 0)
        sys.exit(0)

    elif a.cmd == "inject-fault":
        inject_fault()

    elif a.cmd == "clear-fault":
        clear_fault()

    elif a.cmd == "classify":
        events = parse_log_tail(a.log, TAIL_LINES)
        results = classify_passes(events)
        for pid, ts, v, r in results:
            print(f"{ts} [{v:>9}] {r}")


if __name__ == "__main__":
    main()
