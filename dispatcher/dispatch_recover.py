#!/usr/bin/env python3
"""dispatcher/dispatch_recover.py — dispatcher stall recovery ladder (wq#1410).

research/NIGHTLY_DISPATCH_RESILIENCE.md §1.2 + §3.3: dispatch_health.py (wq#825)
already detects a stalled dispatcher correctly and pages URGENT — but nothing acted
on the page (alerts.db row 794: count=145, status=open since 08-23). This module is
the actor dispatch_health.py was missing: a BOUNDED ladder, advanced one rung per
health-check tick while the alarm condition holds, never looping back, never
touching DISPATCH_DISARM.

Rungs (state in runtime/recover_state.json):
  1. `harness-preflight --all` every migrated repo — fixes a stale/broken stamp
     (§1.4/§1.3), the most common real-world stall cause. Launched DETACHED via lumen-run
     (step 8); the next tick records whether the stamps are fresh, waiting up to
     PREFLIGHT_WAIT_S for the unit, before rung 2 runs.
  2. `systemctl restart` the dispatch timer + `start --no-block` the service — fixes a
     wedged unit or a timer that stopped firing; --no-block so the rung does not wait
     for the whole oneshot pass.
  3. json-parse config.json and every permission-profiles/*.json (incl. repos.d/)
     and name the first file that fails to parse (§1.5 :891 class). Fixes nothing;
     it makes the next human step a `path:line`, not a grep session.
  after rung 3, still unhealthy: STOP. Never re-run rung 1. Page urgent once; the
  ticket itself is filed by aq_escalate's DISPATCHER STALLED rule (bin/aq_escalate.py,
  a separate repo — see that file's second rule), which owns count-based escalation
  and idempotent ticket seeding.

Any healthy pass resets the ladder to rung 0 and sends one `default` "dispatcher
recovered" ntfy — but only if the ladder had actually advanced (no-op spam on an
ordinary healthy pass with no prior incident).

Rollback: FOREMAN_RECOVER_DISABLE=1 makes advance() a no-op (report only). This
module never disarms DISPATCH_DISARM and never writes it.

CLI:
  dispatch_recover.py advance [--dry-run]   # run one rung; called by dispatch_health.py
  dispatch_recover.py reset [--dry-run]     # called by dispatch_health.py on healthy
  dispatch_recover.py status                # print current ladder state
  dispatch_recover.py selftest              # synthetic stall -> classify -> dry-run
                                             # rung 1 -> assert the page would fire
  dispatch_recover.py gate --mode window|nightly [--dry-run]
                                             # wq#1411 §4: prove the next pass can
                                             # dispatch; one OK ntfy or one urgent page
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(os.path.dirname(DISPATCHER_DIR), "harness")
PROFILES_DIR = os.path.join(HARNESS_DIR, "permission-profiles")
CONFIG_PATH = os.path.join(DISPATCHER_DIR, "config.json")
HARNESS_PREFLIGHT_CLI = os.path.join(HARNESS_DIR, "harness-preflight")

sys.path.insert(0, DISPATCHER_DIR)
import vlogs_mirror  # noqa: E402

STATE_PATH = os.environ.get("FOREMAN_RECOVER_STATE",
                            os.path.join(DISPATCHER_DIR, "runtime", "recover_state.json"))
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
# wq#1410 §2.2 item 1: the bare "claude" name resolves under an interactive shell's
# PATH but not the dispatch unit's — the same class of bug this ladder exists to
# recover from. Rung 1 always uses an absolute path so it can't reintroduce it.
CLAUDE_BIN = os.environ.get("FOREMAN_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")
DISPATCH_TIMER = os.environ.get("FOREMAN_DISPATCH_TIMER", "lumen-foreman-dispatch.timer")
DISPATCH_SERVICE = os.environ.get("FOREMAN_DISPATCH_SERVICE", "lumen-foreman-dispatch.service")
DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                             "/home/lumen/autonomy/DISPATCH_DISARM")
DISABLE_ENV = "FOREMAN_RECOVER_DISABLE"
LUMEN_RUN_BIN = os.environ.get("FOREMAN_LUMEN_RUN_BIN", "/home/lumen/bin/lumen-run")
# rung 1 is a detached harness-preflight --all; the next tick waits this long for its unit to
# finish before judging the stamps (a live proof is capped at ~330 s per stale repo).
PREFLIGHT_WAIT_S = int(os.environ.get("FOREMAN_RECOVER_PREFLIGHT_WAIT_S", "900"))
# wq#1410 §3.3.3: resolved here (not left open forever like row aq#794, count=145
# since 08-23) so bin/aq_escalate.py's DISPATCHER STALLED rule sees a per-incident
# count on the next open row, the same "resolve -> next row starts fresh" pattern
# aq_escalate already uses for probe alerts.
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
STALLED_TITLE_LIKE = "%DISPATCHER STALLED%"

MAX_RUNG = 3
STALL_TAG_TITLE = "[DISPATCH] recovery ladder exhausted"
RECOVERED_TITLE = "[DISPATCH] dispatcher recovered"

# wq#1966: advance() runs synchronously inside dispatch_health.py's check_health(),
# which executes under lumen-foreman-health.service — TimeoutStartSec=60, not the
# 1500s the GATE_BUDGET_S machinery above budgets against. Rung 1 used to allow
# harness-preflight --all up to 580s and rung 2's two systemctl calls up to 60s each;
# either can outlive the health unit's own timeout. A SIGTERM there lands before
# check_health() ever reaches _ntfy() or returns, so ExecStartPost (the wq#1184
# dead-man stamp) never runs — the watchdog that exists to catch a stalled dispatcher
# silently stops proving its own liveness, cron_deadman.py starts paging "Dead-man:
# lumen-foreman-health stale" on every subsequent tick, and the real STALLED page
# never goes out. Bounding every rung's subprocess time to fit inside the unit's own
# timeout (with reserve for the ntfy call and file I/O around it) is the same fix
# already applied to the gate path above, moved to the tighter health-tick unit.
HEALTH_UNIT_TIMEOUT_START_SEC = 60    # dispatcher/systemd/lumen-foreman-health.service
HEALTH_TICK_RESERVE_S = 20            # room for check_health()'s own _ntfy (15s) + I/O
LADDER_RUNG_TIMEOUT_S = int(os.environ.get(
    "FOREMAN_LADDER_RUNG_TIMEOUT_S",
    str(HEALTH_UNIT_TIMEOUT_START_SEC - HEALTH_TICK_RESERVE_S)))


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="default", title=RECOVERED_TITLE):
    vlogs_mirror.mirror("dispatch_recover", title, priority, msg)
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---- state.json: {"rung": N, "stopped": bool, "started_at": iso, "history": [...]} ---

def _fresh_state():
    return {"rung": 0, "stopped": False, "started_at": None, "history": [],
            "preflight_launch": None}


def load_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return _fresh_state()
    if not isinstance(st, dict):
        return _fresh_state()
    fresh = _fresh_state()
    fresh["rung"] = int(st.get("rung") or 0)
    fresh["stopped"] = bool(st.get("stopped", False))
    fresh["started_at"] = st.get("started_at")
    hist = st.get("history")
    fresh["history"] = hist if isinstance(hist, list) else []
    launch = st.get("preflight_launch")
    fresh["preflight_launch"] = launch if isinstance(launch, dict) else None
    return fresh


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- rungs -----------------------------------------------------------------------------

def rung1_preflight(dry_run):
    """harness-preflight --all every migrated repo (fixes the stamp class, §1.4).

    Step 8: launched DETACHED through lumen-run, not a foreground call. --all can take
    minutes (a live `claude -p` proof per stale repo) while this runs inside the health
    unit's 60 s budget, so the old foreground call either timed out having proved nothing
    or held the health tick. The rung now returns as soon as the unit is up; advance()
    checks the stamps on the NEXT tick (see _settle_preflight_launch)."""
    cmd = [sys.executable, HARNESS_PREFLIGHT_CLI, "--all", "--claude-bin", CLAUDE_BIN]
    launch = [LUMEN_RUN_BIN, "start", "--name", "recover-preflight", "--"] + cmd
    if dry_run:
        return True, "DRY-RUN would run: " + " ".join(launch)
    try:
        p = subprocess.run(launch, capture_output=True, text=True, timeout=LADDER_RUNG_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"harness-preflight --all failed to launch: {e!r}"
    tail = (p.stdout + p.stderr).strip()[-500:]
    if p.returncode != 0:
        return False, f"lumen-run start rc={p.returncode}: {tail}"
    m = re.search(r"UNIT=(\S+)", p.stdout)
    return True, (f"harness-preflight --all launched detached (unit={m.group(1) if m else '?'}); "
                  f"stamps are checked on the next tick")


def _settle_preflight_launch(launch):
    """Rung 1's next tick. Returns (settled, ok, detail): settled=False while the detached
    unit is still RUNNING and younger than PREFLIGHT_WAIT_S — the ladder waits rather than
    escalating past a proof that has not finished. Otherwise ok says every migrated repo has
    a fresh stamp now (a read-only check_stamps(fix=False), no reprove of its own)."""
    unit, age = launch.get("unit"), None
    try:
        age = (_utcnow() - datetime.fromisoformat(launch["ts"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        pass
    if unit and unit != "?" and (age is None or age < PREFLIGHT_WAIT_S):
        try:
            p = subprocess.run([LUMEN_RUN_BIN, "status", unit], capture_output=True,
                               text=True, timeout=15)
            if p.stdout.strip() == "RUNNING":
                return False, False, f"{unit} still RUNNING after {int(age or 0)}s"
        except (OSError, subprocess.TimeoutExpired):
            pass
    c = check_stamps(fix=False)
    return True, c["ok"], f"unit {unit or '?'} finished; stamps: {c['detail']}"[:1000]


def rung2_restart(dry_run):
    """restart the dispatch timer + start the service (fixes a wedged unit, §1.5).

    wq#1624: these are SYSTEM units, so an unprivileged `systemctl restart/start`
    hits polkit's interactive-auth requirement and always fails with rc=1 ("Access
    denied ... requires interactive authentication") -- this rung has been silently
    non-functional. The lumen user has full passwordless sudo (`sudo -n -l` shows
    `(ALL) NOPASSWD: ALL`), so `sudo -n` is the fix -- same posture as lumen-run's
    `sudo -n systemd-run` pattern elsewhere in this fleet.
    """
    # step 8: `start` on a oneshot blocks until the whole pass finishes (minutes), long
    # past this rung's budget; --no-block queues the start job and returns at once.
    cmds = [["sudo", "-n", "systemctl", "restart", DISPATCH_TIMER],
            ["sudo", "-n", "systemctl", "start", "--no-block", DISPATCH_SERVICE]]
    if dry_run:
        return True, "DRY-RUN would run: " + " && ".join(" ".join(c) for c in cmds)
    details = []
    ok = True
    # wq#1966: two sequential calls each getting the full ladder budget could add up to
    # 2x LADDER_RUNG_TIMEOUT_S — split it so the rung as a whole still fits.
    per_call_timeout = max(5, LADDER_RUNG_TIMEOUT_S // len(cmds))
    for c in cmds:
        try:
            p = subprocess.run(c, capture_output=True, text=True, timeout=per_call_timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            ok = False
            details.append(f"{' '.join(c)} -> EXC {e!r}")
            continue
        if p.returncode != 0:
            ok = False
        details.append(f"{' '.join(c)} -> rc={p.returncode} {p.stderr.strip()[:200]}")
    return ok, "; ".join(details)


def _json_files_to_check():
    files = [CONFIG_PATH]
    for base in (PROFILES_DIR, os.path.join(PROFILES_DIR, "repos.d")):
        try:
            names = sorted(f for f in os.listdir(base) if f.endswith(".json"))
        except OSError:
            continue
        files.extend(os.path.join(base, n) for n in names)
    return files


def rung3_diagnose(dry_run):
    """json-parse config.json + every permission-profiles/*.json (incl. repos.d/) and
    name the first unparseable file:line (§1.5 :891 class). Fixes nothing on purpose —
    a JSON syntax error needs a human edit; this just turns a grep session into a
    file:line the page already names."""
    checked = _json_files_to_check()
    broken = []
    for path in checked:
        try:
            with open(path) as f:
                json.load(f)
        except OSError as e:
            broken.append(f"{path}: unreadable ({e})")
        except json.JSONDecodeError as e:
            broken.append(f"{path}:{e.lineno}: {e.msg}")
    if dry_run:
        return (not broken), f"DRY-RUN checked {len(checked)} file(s); broken={broken}"
    if broken:
        return False, "broken JSON: " + "; ".join(broken)
    return True, f"all {len(checked)} JSON file(s) parse cleanly (names nothing to fix)"


RUNGS = {
    1: ("harness-preflight --all", rung1_preflight),
    2: ("restart dispatch timer+service", rung2_restart),
    3: ("diagnose config/profile JSON", rung3_diagnose),
}


# ---- ladder actor -----------------------------------------------------------------------

def advance(dry_run=False):
    """Run exactly one rung forward from the ladder's current position. Called once
    per dispatch_health.py tick while the alarm condition holds. Idempotent per tick:
    once stopped (post rung 3, still unhealthy) it reports and does nothing further
    until reset() by a healthy pass."""
    if os.environ.get(DISABLE_ENV) == "1":
        return {"action": "disabled", "rung": 0,
                "detail": f"{DISABLE_ENV}=1 — ladder not advanced"}

    st = load_state()
    if st["stopped"]:
        return {"action": "stopped", "rung": st["rung"],
                "detail": f"ladder already stopped at rung {st['rung']} — "
                          "waiting for a healthy pass to reset it"}

    if os.path.exists(DISARM_PATH):
        return {"action": "paused", "rung": st["rung"],
                "detail": f"DISPATCH_DISARM present ({DISARM_PATH}) — ladder paused, "
                          "not advancing (never touched by this module)"}

    launch = st.get("preflight_launch")
    if launch:  # rung 1 launched harness-preflight detached on the previous tick
        settled, stamps_ok, stamp_detail = _settle_preflight_launch(launch)
        if not settled:
            return {"action": "waiting", "rung": st["rung"], "detail": stamp_detail}
        st["preflight_launch"] = None
        if not dry_run:
            st["history"].append({"ts": _utcnow().isoformat(timespec="seconds"), "rung": 1,
                                  "name": "preflight stamp check", "ok": stamps_ok,
                                  "detail": stamp_detail})
            save_state(st)

    next_rung = st["rung"] + 1
    if next_rung > MAX_RUNG:
        st["stopped"] = True
        if not dry_run:
            save_state(st)
        msg = (f"Recovery ladder exhausted after rung {MAX_RUNG} "
               f"({RUNGS[MAX_RUNG][0]}) — dispatcher still unhealthy. "
               "aq_escalate's DISPATCHER STALLED rule owns filing the fleet-fires "
               "ticket; this is the immediate page.")
        if dry_run:
            print(f"WOULD PAGE (ladder exhausted): {msg}")
        else:
            _ntfy(msg, priority="urgent", title=STALL_TAG_TITLE)
        return {"action": "exhausted", "rung": MAX_RUNG, "detail": msg}

    name, fn = RUNGS[next_rung]
    ok, detail = fn(dry_run)
    if not dry_run:
        if not st.get("started_at"):
            st["started_at"] = _utcnow().isoformat(timespec="seconds")
        st["rung"] = next_rung
        launched = re.search(r"unit=(\S+?)\)", detail) if next_rung == 1 and ok else None
        if launched:
            st["preflight_launch"] = {"ts": _utcnow().isoformat(timespec="seconds"),
                                      "unit": launched.group(1)}
        st["history"].append({"ts": _utcnow().isoformat(timespec="seconds"),
                              "rung": next_rung, "name": name, "ok": ok,
                              "detail": detail[:2000]})
        save_state(st)
    return {"action": "ran_rung", "rung": next_rung, "name": name, "ok": ok, "detail": detail}


def _resolve_stalled_alert_rows():
    """Close any open DISPATCHER STALLED alerts.db row(s) so the next stall's page
    starts a fresh row with a per-incident count, not row aq#794's lifetime 145.
    Fail-open: any DB problem is swallowed, never raised into reset()'s caller."""
    conn = None
    try:
        conn = sqlite3.connect(AQ_DB, timeout=15)
        with conn:
            cur = conn.execute(
                "UPDATE alerts SET status='resolved', resolved_ts=? "
                "WHERE title LIKE ? AND status='open'",
                (_utcnow().isoformat(timespec="seconds"), STALLED_TITLE_LIKE))
            return cur.rowcount
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def reset(dry_run=False, reason="dispatcher recovered"):
    """A healthy pass resets the ladder. Only pages if the ladder had actually
    advanced or stopped — an ordinary healthy pass with no prior incident is silent."""
    st = load_state()
    was_active = st["rung"] > 0 or st["stopped"]
    if was_active:
        msg = f"{reason} — recovery ladder reset (was at rung {st['rung']})."
        if dry_run:
            print(f"WOULD NTFY (recovered): {msg}")
        else:
            _ntfy(msg, priority="default", title=RECOVERED_TITLE)
            _resolve_stalled_alert_rows()
    if not dry_run:
        save_state(_fresh_state())
    return was_active


# ---- selftest (§3.3.5): wired into the weekly gate elsewhere; here it's a plain,
# no-side-effect check runnable any time. ------------------------------------------------

def selftest():
    """Synthetic 2-pass stall -> dispatch_health.classify_passes/check_health (dry-run)
    -> assert the page WOULD fire -> advance() in dry-run and assert rung 1 runs clean.
    Never touches the real log, state file, or ntfy. Returns True on pass."""
    import dispatch_health

    ok = True
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "dispatcher.log")
        now = _utcnow()
        lines = []
        for i in (1, 2):
            pid = f"fdisp-selftest{i}"
            lines.append(json.dumps({"ts": now.isoformat(timespec="seconds"),
                                     "pass_id": pid, "event": "pass_start",
                                     "dry_run": False}))
            lines.append(json.dumps({"ts": now.isoformat(timespec="seconds"),
                                     "pass_id": pid, "event": "pass_end",
                                     "candidates": 12, "dispatchable": 5, "spawned": 0}))
        with open(log_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        events = dispatch_health.parse_log_tail(log_path, dispatch_health.TAIL_LINES)
        results = dispatch_health.classify_passes(events)
        verdicts = [v for _pid, _ts, v, _r in results]
        if verdicts != ["unhealthy", "unhealthy"]:
            print(f"SELFTEST FAIL: expected 2x unhealthy, classified {verdicts}")
            ok = False

        fired, detail = dispatch_health.check_health(log_path, threshold=2, dry_run=True)
        if not fired:
            print(f"SELFTEST FAIL: dry-run check_health did not fire: {detail}")
            ok = False
        else:
            print(f"SELFTEST: page would fire — {detail.splitlines()[0]}")

    state_path = os.path.join(tempfile.mkdtemp(), "recover_state.json")
    global STATE_PATH
    real_state_path = STATE_PATH
    STATE_PATH = state_path
    try:
        result = advance(dry_run=True)
    finally:
        STATE_PATH = real_state_path
    if result["action"] != "ran_rung" or result["rung"] != 1 or not result["ok"]:
        print(f"SELFTEST FAIL: dry-run advance() did not run a clean rung 1: {result}")
        ok = False
    else:
        print(f"SELFTEST: rung 1 dry-run ok — {result['detail'].splitlines()[0]}")

    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return ok


# =========================================================================================
# ---- window gate (wq#1411, research/NIGHTLY_DISPATCH_RESILIENCE.md §4) -------------------
# =========================================================================================
#
# The Max reset lands Thursday ~04:00 PT (governor limits.weekly_all.resets_at). The
# ladder above reacts to a stall AFTER it has cost passes; the gate below runs BEFORE
# the window (Thu 00:30 PT) and every night (22:00 PT) and proves, end to end, that
# the next pass CAN dispatch — then says so with one default-priority ntfy, so a
# silent night is never ambiguous.
#
# Seven checks; the first two are recoverable in-gate (they are exactly the stamp
# and wedged-unit classes the ladder's rungs fix), the rest are reported as-is:
#   stamps    every repos.d/<repo>.json repo has a fresh preflight stamp under
#             harness_preflight.dispatch_gate_check; stale -> harness-preflight NOW
#             (absolute CLAUDE_BIN), recheck.
#   dryrun    foreman_dispatcher.py run --once --dry-run into a scratch log completes
#             with pass_end dispatchable>0 and spawned>0 (in dry-run `spawned` counts
#             would_spawn events — foreman_dispatcher.py stage 10). would_spawn=0 ->
#             top skip reasons in the page.
#   governor  state.json readable, poll fresh (updated_at within
#             floors.stale_fail_closed_seconds), stop not active; --mode window adds:
#             weekly_all.resets_at within the next GATE_RESET_HORIZON_S.
#   breaker   circuit_breaker.evaluate() not tripped.
#   disarm    DISPATCH_DISARM absent.
#   auth      `claude auth status` reports loggedIn under the dispatch unit's own
#             PATH, run through `systemd-run --pipe` (a real transient unit, not this
#             process's environment) — the §1.3 class, proven the way the unit runs it.
#   selftest  selftest() above (§3.3.5) passes.
#
# One pass is bounded by GATE_BUDGET_S (wq#1460): the checks above have per-call timeouts
# but no aggregate one, and check_stamps alone costs 330s per stale repo. Running out the
# unit's TimeoutStartSec would page NOTHING (SIGTERM precedes both ntfys and skips
# ExecStartPost), so the gate fails fast on its own clock instead: an unrun check is a
# failed check, and the urgent page always goes out.
#
# Rungs used here call the rung functions directly and never touch recover_state.json:
# the gate is a separate actor from the health-tick ladder and must not advance or
# reset it. FOREMAN_RECOVER_DISABLE=1 makes the gate report-only (no reproves, no
# restarts). DISPATCH_DISARM is read, never written.

# Same env hook circuit_breaker.py:53 honours, so a worktree checkout can be pointed
# at the live tree's state/stamps for a drill without editing anything.
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", os.path.dirname(DISPATCHER_DIR))
GOVERNOR_STATE = os.environ.get("FOREMAN_STATE_JSON", os.path.join(FOREMAN_ROOT, "state.json"))
GOVERNOR_FLOORS = os.environ.get("FOREMAN_GOVERNOR_FLOORS",
                                 os.path.join(FOREMAN_ROOT, "governor", "floors.json"))
DISPATCHER_CLI = os.path.join(DISPATCHER_DIR, "foreman_dispatcher.py")
ORCHESTRATOR_DIR = os.path.join(FOREMAN_ROOT, "orchestrator")
GATE_REPORT_PATH = os.environ.get("FOREMAN_GATE_REPORT",
                                  os.path.join(DISPATCHER_DIR, "runtime", "gate_last.json"))
GATE_RESET_HORIZON_S = int(os.environ.get("FOREMAN_GATE_RESET_HORIZON_S", str(6 * 3600)))
GATE_STALE_DEFAULT_S = 600            # governor_core DEFAULT_FLOORS stale_fail_closed_seconds
GATE_DRYRUN_TIMEOUT_S = 540           # under the unit's TimeoutStartSec, leaves room for rungs
GATE_PREFLIGHT_TIMEOUT_S = 330        # harness_preflight.LIVE_PROOF_TIMEOUT + slack
GATE_LOCK_RETRY_S = int(os.environ.get("FOREMAN_GATE_LOCK_RETRY_S", "60"))
# Aggregate wall-clock budget for one gate pass (wq#1460). The per-check timeouts above
# do NOT bound a pass: check_stamps reproves stale repos sequentially at up to
# GATE_PREFLIGHT_TIMEOUT_S each, so its cost is 330s x migrated-repo-count and grows every
# time a repo is migrated; add dryrun (540) + rungs + the dryrun recheck (540) and the
# worst case already exceeds lumen-foreman-gate@.service's TimeoutStartSec=1500 at three
# repos. A systemd timeout kill is the one outcome the gate must never have: SIGTERM lands
# before either ntfy and skips ExecStartPost (the wq#1184 cron dead-man), so a wedged unit —
# exactly what the gate exists to catch — would page nothing at all. So the gate bounds
# ITSELF: every check draws from this budget, and once it is spent the remaining checks are
# reported unrun (a failure) and the urgent page still goes out with time to spare.
# INVARIANT, asserted by test_gate_budget_fits_under_unit_timeout_start_sec:
#   GATE_BUDGET_S + GATE_BUDGET_RESERVE_S < TimeoutStartSec of lumen-foreman-gate@.service.
GATE_BUDGET_S = int(os.environ.get("FOREMAN_GATE_BUDGET_S", "1200"))
GATE_BUDGET_RESERVE_S = 60            # held back for the ntfy + report write + ExecStartPost
GATE_MIN_CHECK_S = 20                 # below this, don't start another check at all
GATE_TOP_SKIPS = 5
GATE_MODES = ("window", "nightly")
GATE_OK_TITLE = {"window": "[DISPATCH] window gate OK", "nightly": "[DISPATCH] nightly gate OK"}
GATE_FAIL_TITLE = {"window": "[DISPATCH] window gate FAILED",
                   "nightly": "[DISPATCH] nightly gate FAILED"}
DISPATCH_UNIT_FOR_PATH = os.environ.get("FOREMAN_GATE_PATH_UNIT", DISPATCH_SERVICE)
_PT = "America/Los_Angeles"


def _pt(dt_or_iso):
    """Render a UTC datetime/ISO string in Pacific for the page (fleet convention)."""
    try:
        from zoneinfo import ZoneInfo
        d = dt_or_iso if isinstance(dt_or_iso, datetime) else datetime.fromisoformat(dt_or_iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(ZoneInfo(_PT)).strftime("%a %H:%M %Z")
    except Exception:  # noqa: BLE001 — formatting must never break a check
        return str(dt_or_iso)


def _check(name, ok, detail, recoverable=False):
    return {"name": name, "ok": bool(ok), "detail": detail, "recoverable": recoverable}


class GateBudget:
    """One wall-clock budget shared by every check in a gate pass (wq#1460).

    Checks ask for a subprocess timeout with timeout_for(); they get the smaller of what
    they wanted and what is left, or None when there is not enough left to be worth
    starting. Monotonic, so a clock step mid-pass cannot hand out a bigger budget than
    the unit's TimeoutStartSec allows.
    """

    def __init__(self, total_s=None, clock=time.monotonic):
        self._clock = clock
        self.total = GATE_BUDGET_S if total_s is None else int(total_s)
        self._started = clock()

    def spent(self):
        return self._clock() - self._started

    def remaining(self):
        """Seconds a check may still consume — the reserve is not spendable."""
        return self.total - GATE_BUDGET_RESERVE_S - self.spent()

    def timeout_for(self, want_s):
        """Subprocess timeout clamped to the budget; None means don't start it."""
        left = int(self.remaining())
        return None if left < GATE_MIN_CHECK_S else min(int(want_s), left)

    def exhausted(self):
        return self.timeout_for(GATE_MIN_CHECK_S) is None

    def note(self):
        return f"gate budget exhausted ({self.spent():.0f}s of {self.total}s spent)"


def _migrated_repo_paths():
    """Same resolution harness/harness-preflight --all uses: one path per
    permission-profiles/repos.d/<name>.json, mapped through router.LANE_REPO so the
    stamp is looked up under the path the dispatcher will use."""
    sys.path.insert(0, HARNESS_DIR)
    sys.path.insert(0, ORCHESTRATOR_DIR)
    import permission_profiles  # noqa: E402
    import router  # noqa: E402
    by_name = {permission_profiles.repo_name(p): p for p in router.LANE_REPO.values()}
    try:
        names = sorted(f[:-len(".json")] for f in os.listdir(permission_profiles.REPOS_DIR)
                       if f.endswith(".json"))
    except OSError:
        return []
    return [by_name.get(n, f"/home/lumen/{n}") for n in names]


def _reprove_repo(repo, dry_run, budget=None):
    cmd = [sys.executable, HARNESS_PREFLIGHT_CLI, repo, "--claude-bin", CLAUDE_BIN]
    if dry_run:
        return False, "DRY-RUN would run: " + " ".join(cmd)
    timeout = GATE_PREFLIGHT_TIMEOUT_S if budget is None \
        else budget.timeout_for(GATE_PREFLIGHT_TIMEOUT_S)
    if timeout is None:
        return False, budget.note()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"harness-preflight failed to run: {e!r}"
    tail = (p.stdout + p.stderr).strip()[-400:]
    return p.returncode == 0, f"rc={p.returncode} {tail}"


def check_stamps(fix=True, dry_run=False, budget=None):
    """Every migrated repo has a fresh stamp; a stale/missing one is reproved right
    here (the §4.1 'not at 03:00' rule) and rechecked. Reproving is the one check whose
    cost scales with the fleet (330s x stale repos), so it draws from the pass budget:
    once that is spent the still-stale repos are reported, not reproved (wq#1460)."""
    sys.path.insert(0, HARNESS_DIR)
    import harness_preflight  # noqa: E402
    repos = _migrated_repo_paths()
    if not repos:
        return _check("stamps", True, "no migrated repos (no repos.d/*.json) — nothing to prove")
    lines, all_ok, reproved = [], True, []
    for repo in repos:
        name = os.path.basename(repo.rstrip("/"))
        try:
            ok, reason = harness_preflight.dispatch_gate_check(repo)
        except Exception as e:  # noqa: BLE001 — a broken profile file is a finding, not a crash
            ok, reason = False, f"profile_unreadable: {e!r}"
        if ok:
            lines.append(f"{name}: fresh" + (f" ({reason})" if reason else ""))
            continue
        if not fix:
            all_ok = False
            lines.append(f"{name}: STALE — {reason}")
            continue
        if budget is not None and budget.exhausted():
            all_ok = False
            lines.append(f"{name}: STALE ({reason}); not reproved — {budget.note()}")
            continue
        rok, rdetail = _reprove_repo(repo, dry_run, budget=budget)
        if rok:
            try:
                ok2, reason2 = harness_preflight.dispatch_gate_check(repo)
            except Exception as e:  # noqa: BLE001
                ok2, reason2 = False, repr(e)
            if ok2:
                reproved.append(name)
                lines.append(f"{name}: was stale ({reason}) -> reproved now, fresh")
                continue
            all_ok = False
            lines.append(f"{name}: reproved but STILL stale — {reason2}")
        else:
            all_ok = False
            lines.append(f"{name}: STALE ({reason}); reprove FAILED — {rdetail}")
    c = _check("stamps", all_ok, "; ".join(lines), recoverable=True)
    c["reproved"] = reproved
    return c


def _run_dispatcher_dry_run(scratch_log, budget=None):
    env = dict(os.environ)
    env["FOREMAN_DISPATCHER_LOG"] = scratch_log
    cmd = [sys.executable, DISPATCHER_CLI, "run", "--once", "--dry-run"]
    timeout = GATE_DRYRUN_TIMEOUT_S if budget is None else budget.timeout_for(GATE_DRYRUN_TIMEOUT_S)
    if timeout is None:
        return None, budget.note(), []
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=FOREMAN_ROOT, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"dry-run failed to run: {e!r}", []
    events = []
    try:
        with open(scratch_log) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return p.returncode, (p.stdout + p.stderr).strip()[-300:], events


def _summarize_dry_run(events):
    """(verdict, detail) from one dry-run pass's decision log. verdict in
    {'ok','lock_busy','fail'}."""
    pass_end = next((e for e in events if e.get("event") == "pass_end"), None)
    halts = [e for e in events if e.get("event") == "halt"]
    errors = [e for e in events if e.get("event") == "error" and e.get("stage") != "config_lane"]
    for h in halts:
        stage, reason = h.get("stage", ""), h.get("reason", "") or ""
        if stage == "lock" and "lock_busy" in reason:
            return "lock_busy", "dispatcher lock busy (a real pass is running)"
        if stage == "concurrency" and "no_slots" in reason:
            return "ok", "halted at concurrency/no_slots — every slot is busy, nothing to prove"
        if stage in ("kill_switch", "circuit_breaker"):
            return "fail", f"halt stage={stage} reason={reason}"
    if errors:
        e = errors[0]
        return "fail", f"error stage={e.get('stage')} reason={str(e.get('reason'))[:200]}"
    if pass_end is None:
        return "fail", "no pass_end in dry-run log (pass did not complete)"
    cand = int(pass_end.get("candidates") or 0)
    dispatchable = pass_end.get("dispatchable")
    dispatchable = int(dispatchable) if dispatchable is not None else cand
    would_spawn = int(pass_end.get("spawned") or 0)   # dry-run counts would_spawn here
    n_ws = sum(1 for e in events if e.get("event") == "would_spawn")
    would_spawn = max(would_spawn, n_ws)
    head = f"candidates={cand} dispatchable={dispatchable} would_spawn={would_spawn}"
    if dispatchable > 0 and would_spawn > 0:
        return "ok", head
    from collections import Counter
    skips = Counter((e.get("stage", "?"), e.get("reason", "?"))
                    for e in events if e.get("event") == "skip")
    top = ", ".join(f"{s}/{r}={n}" for (s, r), n in skips.most_common(GATE_TOP_SKIPS))
    why = ("queue has no candidates at all" if cand == 0 else
           "every candidate is structurally undispatchable" if dispatchable == 0 else
           "dispatchable tickets exist but none would spawn")
    return "fail", f"{head} — {why}; top skips: {top or 'none'}"


def check_dryrun(dry_run=False, budget=None):
    """§4.2: a real decision pass, zero side effects, must report it WOULD spawn."""
    if dry_run:
        return _check("dryrun", True, "DRY-RUN would run foreman_dispatcher.py run --once "
                      "--dry-run into a scratch log", recoverable=True)
    with tempfile.TemporaryDirectory() as td:
        scratch = os.path.join(td, "dispatcher-dryrun.log")
        rc, tail, events = _run_dispatcher_dry_run(scratch, budget=budget)
        if rc is None:
            return _check("dryrun", False, tail, recoverable=True)
        verdict, detail = _summarize_dry_run(events)
        if verdict == "lock_busy":
            # The retry sleep spends budget like anything else; skip it if that would
            # leave no room for the second pass (wq#1460).
            if budget is not None and budget.remaining() < GATE_LOCK_RETRY_S + GATE_MIN_CHECK_S:
                return _check("dryrun", False, f"{detail}; no retry — {budget.note()}",
                              recoverable=True)
            time.sleep(GATE_LOCK_RETRY_S)
            rc, tail, events = _run_dispatcher_dry_run(scratch + ".2", budget=budget)
            if rc is None:
                return _check("dryrun", False, tail, recoverable=True)
            verdict, detail = _summarize_dry_run(events)
            if verdict == "lock_busy":
                detail = f"{detail} on both attempts ({GATE_LOCK_RETRY_S}s apart)"
        if rc not in (0, None) and verdict == "ok":
            detail += f" (dispatcher rc={rc}: {tail})"
        return _check("dryrun", verdict == "ok", detail, recoverable=True)


def _stale_seconds():
    try:
        with open(GOVERNOR_FLOORS) as f:
            v = json.load(f).get("stale_fail_closed_seconds")
        return int(v) if v else GATE_STALE_DEFAULT_S
    except (OSError, ValueError, TypeError):
        return GATE_STALE_DEFAULT_S


def check_governor(mode, now=None):
    """§4.3: governor state readable + fresh; in window mode the weekly reset must be
    ahead of us and inside GATE_RESET_HORIZON_S (else the window this gate exists
    for is not the window the governor thinks it is)."""
    now = now or _utcnow()
    try:
        with open(GOVERNOR_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError) as e:
        return _check("governor", False, f"state unreadable: {e!r} ({GOVERNOR_STATE})")
    if not isinstance(st, dict):
        return _check("governor", False, "state.json is not an object")
    problems, notes = [], []
    upd = st.get("updated_at")
    try:
        age = (now - datetime.fromisoformat(upd)).total_seconds()
    except (TypeError, ValueError):
        age = None
    stale_s = _stale_seconds()
    if age is None:
        problems.append("updated_at missing/unparseable")
    elif age > stale_s:
        problems.append(f"poll stale: updated_at {int(age)}s ago (> {stale_s}s) — "
                        "governor daemon not running?")
    else:
        notes.append(f"poll {max(0, int(age))}s old")
    if st.get("stale"):
        problems.append("state.stale=true (daemon failed closed)")
    if st.get("stop_active"):
        problems.append("STOP kill-switch active")
    limits = st.get("limits") if isinstance(st.get("limits"), dict) else {}
    weekly = limits.get("weekly_all") if isinstance(limits.get("weekly_all"), dict) else {}
    resets_at = weekly.get("resets_at")
    pct = weekly.get("percent")
    reset_dt = None
    try:
        reset_dt = datetime.fromisoformat(resets_at) if resets_at else None
    except (TypeError, ValueError):
        reset_dt = None
    if reset_dt is not None:
        delta = (reset_dt - now).total_seconds()
        notes.append(f"weekly_all {pct}% resets {_pt(reset_dt)} ({int(delta // 60)}m away)")
        if mode == "window" and not (0 < delta <= GATE_RESET_HORIZON_S):
            problems.append(f"weekly_all.resets_at not within the next "
                            f"{GATE_RESET_HORIZON_S // 3600}h ({int(delta // 60)}m away)")
    else:
        msg = "weekly_all.resets_at missing/unparseable"
        (problems if mode == "window" else notes).append(msg)
    ok = not problems
    return _check("governor", ok, "; ".join(problems + notes))


def check_breaker():
    try:
        import circuit_breaker
        tripped, stats = circuit_breaker.evaluate()
    except Exception as e:  # noqa: BLE001
        return _check("breaker", False, f"evaluate() raised {e!r}")
    detail = (f"tripped={tripped} {stats.get('failures')}/{stats.get('total')} failed in "
              f"last {stats.get('window_minutes')}m")
    return _check("breaker", not tripped, detail)


def check_disarm():
    present = os.path.exists(DISARM_PATH)
    return _check("disarm", not present,
                  f"DISPATCH_DISARM {'PRESENT' if present else 'absent'} ({DISARM_PATH})")


def _dispatch_unit_path():
    """PATH the live dispatch unit actually runs with (its drop-ins included), so the
    auth proof tests the environment a spawn gets — not this process's."""
    try:
        p = subprocess.run(["systemctl", "show", "-p", "Environment", "--value",
                            DISPATCH_UNIT_FOR_PATH], capture_output=True, text=True, timeout=15)
        for tok in p.stdout.split():
            if tok.startswith("PATH="):
                return tok[len("PATH="):], f"from {DISPATCH_UNIT_FOR_PATH}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.environ.get("PATH", ""), "fallback: this process's PATH (unit env unreadable)"


def _run_under_unit_env(path, argv, timeout=60):
    """Run argv as a transient system unit with exactly `path` as PATH. Bare names in
    systemd-run's own argv are resolved by the manager's search path, not the unit's,
    so the command is wrapped in /bin/sh -c — the shell inside resolves against the
    PATH under test. Returns (rc, stdout, stderr); rc None if systemd-run itself could
    not be started."""
    cmd = ["sudo", "-n", "systemd-run", "--pipe", "--wait", "--collect", "-q",
           "-p", "User=lumen", "-p", f"WorkingDirectory={FOREMAN_ROOT}",
           "-E", f"PATH={path}", "-E", "HOME=/home/lumen",
           "/bin/sh", "-c", " ".join(argv)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "", repr(e)
    return p.returncode, p.stdout, p.stderr


def check_auth():
    """§4.3: `claude auth status` under the unit PATH via systemd-run --pipe."""
    path, src = _dispatch_unit_path()
    rc, out, err = _run_under_unit_env(path, ["claude", "auth", "status"])
    if rc is None:
        return _check("auth", False, f"systemd-run could not start: {err[:200]} (PATH {src})")
    if rc != 0:
        return _check("auth", False,
                      f"claude auth status rc={rc} under unit PATH ({src}): "
                      f"{(err or out).strip()[:200]}")
    try:
        info = json.loads(out)
    except ValueError:
        return _check("auth", False, f"claude auth status printed non-JSON: {out.strip()[:200]}")
    logged_in = bool(info.get("loggedIn"))
    detail = (f"loggedIn={logged_in} authMethod={info.get('authMethod')} "
              f"subscription={info.get('subscriptionType')} (PATH {src})")
    return _check("auth", logged_in, detail)


def check_selftest():
    import contextlib
    import io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = selftest()
    except Exception as e:  # noqa: BLE001
        return _check("selftest", False, f"selftest raised {e!r}")
    last = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return _check("selftest", ok, (last[-1] if last else "no output")[:200])


def _recover_dryrun(dry_run, budget=None):
    """The two rungs that can unwedge a pass that would not spawn (stamps already
    reproved themselves in check_stamps). Direct rung calls: recover_state.json is the
    health ladder's, not the gate's."""
    steps = []
    for rung in (2, 3):
        name, fn = RUNGS[rung]
        if budget is not None and budget.exhausted():
            steps.append(f"rung{rung} {name}: not run — {budget.note()}")
            continue
        ok, detail = fn(dry_run)
        steps.append(f"rung{rung} {name}: {'ok' if ok else 'FAILED'} — {detail[:300]}")
    return steps


def run_gate(mode="window", dry_run=False, now=None, budget=None):
    """Run every check; recover what is recoverable; page exactly once.
    Returns the report dict (also written to GATE_REPORT_PATH).

    Every check draws from one wall-clock budget (wq#1460) so the pass always reaches its
    ntfy inside the unit's TimeoutStartSec. A check with no budget left is not started and
    is reported unrun — which fails the gate, because unrun is not proven."""
    if mode not in GATE_MODES:
        raise ValueError(f"mode must be one of {GATE_MODES}")
    started = now or _utcnow()
    disabled = os.environ.get(DISABLE_ENV) == "1"
    fix = not disabled
    budget = GateBudget() if budget is None else budget
    ordered = (("stamps", lambda: check_stamps(fix=fix, dry_run=dry_run, budget=budget)),
               ("dryrun", lambda: check_dryrun(dry_run=dry_run, budget=budget)),
               ("governor", lambda: check_governor(mode, now=started)),
               ("breaker", check_breaker),
               ("disarm", check_disarm),
               ("auth", check_auth),
               ("selftest", check_selftest))
    checks = []
    for name, fn in ordered:
        if budget.exhausted():
            checks.append(_check(name, False, f"not run — {budget.note()}"))
            continue
        checks.append(fn())
    recovery = []
    dr = next(c for c in checks if c["name"] == "dryrun")
    if not dr["ok"] and fix and not dry_run and not budget.exhausted():
        recovery = _recover_dryrun(dry_run, budget=budget)
        dr2 = (check_dryrun(dry_run=dry_run, budget=budget) if not budget.exhausted()
               else _check("dryrun", False, f"not rechecked — {budget.note()}", recoverable=True))
        dr2["detail"] = f"after rungs: {dr2['detail']} | first attempt: {dr['detail']}"
        checks[checks.index(dr)] = dr2
    failed = [c for c in checks if not c["ok"]]
    ok = not failed
    report = {"mode": mode, "ok": ok, "dry_run": dry_run, "disabled": disabled,
              "started_at": started.isoformat(timespec="seconds"),
              "finished_at": _utcnow().isoformat(timespec="seconds"),
              "checks": checks, "recovery": recovery,
              "budget": {"total_s": budget.total, "spent_s": round(budget.spent(), 1),
                         "exhausted": budget.exhausted()},
              "reproved": next((c.get("reproved") for c in checks
                                if c["name"] == "stamps"), [])}
    if ok:
        fixed = report["reproved"]
        msg = (f"{mode} gate OK at {_pt(started)}: stamps fresh"
               + (f" (reproved {', '.join(fixed)})" if fixed else "")
               + f"; dry-run {dr['detail'].split(' — ')[0]}; "
               + next(c['detail'] for c in checks if c['name'] == 'governor')
               + "; breaker armed; DISARM absent; claude auth ok under unit PATH; "
                 "selftest pass.")
        title, prio = GATE_OK_TITLE[mode], "default"
    else:
        names = ", ".join(c["name"] for c in failed)
        body = "\n".join(f"- {c['name']}: {c['detail'][:400]}" for c in failed)
        rec = ("\nrecovery: " + " | ".join(recovery)) if recovery else ""
        bud = f"\n{budget.note()} — checks left unrun are NOT proven" if budget.exhausted() else ""
        msg = (f"{mode} gate FAILED at {_pt(started)} — {len(failed)} check(s): {names}\n"
               f"{body}{rec}{bud}")
        title, prio = GATE_FAIL_TITLE[mode], "urgent"
    report["ntfy"] = {"title": title, "priority": prio, "message": msg}
    if dry_run:
        print(f"WOULD NTFY [{prio}] {title}: {msg}")
    else:
        _ntfy(msg, priority=prio, title=title)
        try:
            d = os.path.dirname(GATE_REPORT_PATH)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = GATE_REPORT_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(report, f, indent=1)
            os.replace(tmp, GATE_REPORT_PATH)
        except OSError:
            pass
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dispatcher stall recovery ladder (wq#1410)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("advance", help="run one rung forward if the ladder isn't stopped")
    a.add_argument("--dry-run", action="store_true")

    r = sub.add_parser("reset", help="reset the ladder (a healthy pass calls this)")
    r.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="print the current ladder state")
    sub.add_parser("selftest", help="synthetic stall -> classify -> dry-run rung 1")

    g = sub.add_parser("gate", help="wq#1411 §4 pre-window / nightly dispatch gate")
    g.add_argument("--mode", choices=GATE_MODES, default="window",
                   help="window = Thu pre-reset (adds the 6h resets_at check); "
                        "nightly = same gate without it")
    g.add_argument("--dry-run", action="store_true",
                   help="run the read-only checks, no reproves/restarts/ntfy; "
                        "exit 2 if any check fails")

    args = ap.parse_args(argv)

    if args.cmd == "advance":
        result = advance(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok", True) else 1)
    elif args.cmd == "reset":
        was_active = reset(dry_run=args.dry_run)
        print(json.dumps({"reset": True, "was_active": was_active}))
    elif args.cmd == "status":
        print(json.dumps(load_state(), indent=2))
    elif args.cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    elif args.cmd == "gate":
        report = run_gate(mode=args.mode, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in report.items() if k != "ntfy"}, indent=2))
        # Real run: the ntfy IS the verdict (same rule as dispatch_health.py, wq#1012);
        # exit 0 so a failed CHECK does not also fire the unit's OnFailure page — that
        # hook is reserved for the gate itself crashing. --dry-run keeps the nonzero
        # exit so tests/CI can read the verdict.
        sys.exit(0 if (report["ok"] or not args.dry_run) else 2)


if __name__ == "__main__":
    main()
