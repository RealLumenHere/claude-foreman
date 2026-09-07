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
     (§1.4/§1.3), the most common real-world stall cause.
  2. `systemctl restart` the dispatch timer + `start` the service — fixes a wedged
     unit or a timer that stopped firing.
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
  dispatch_recover.py gate [--dry-run]      # wq#1411 high-value-window pre-flight gate,
                                             # research/NIGHTLY_DISPATCH_RESILIENCE.md §4 —
                                             # see gate()'s docstring below
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DISPATCHER_DIR)
HARNESS_DIR = os.path.join(REPO_ROOT, "harness")
ORCHESTRATOR_DIR = os.path.join(REPO_ROOT, "orchestrator")
PROFILES_DIR = os.path.join(HARNESS_DIR, "permission-profiles")
CONFIG_PATH = os.path.join(DISPATCHER_DIR, "config.json")
HARNESS_PREFLIGHT_CLI = os.path.join(HARNESS_DIR, "harness-preflight")
FOREMAN_DISPATCHER_CLI = os.path.join(DISPATCHER_DIR, "foreman_dispatcher.py")

sys.path.insert(0, DISPATCHER_DIR)

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
# wq#1410 §3.3.3: resolved here (not left open forever like row aq#794, count=145
# since 08-23) so bin/aq_escalate.py's DISPATCHER STALLED rule sees a per-incident
# count on the next open row, the same "resolve -> next row starts fresh" pattern
# aq_escalate already uses for probe alerts.
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
STALLED_TITLE_LIKE = "%DISPATCHER STALLED%"

MAX_RUNG = 3
STALL_TAG_TITLE = "[DISPATCH] recovery ladder exhausted"
RECOVERED_TITLE = "[DISPATCH] dispatcher recovered"

# ---- window gate (wq#1411, §4) -----------------------------------------------------
# The dispatch unit's own Environment=PATH (dispatcher/systemd/lumen-foreman-
# dispatch.service) — the gate's claude-auth check must run inside this exact
# environment, not an interactive login shell's, or it proves nothing about the class
# of failure this ticket exists to catch ahead of time (§1.3: a binary/flag that
# resolves under an interactive shell but not under systemd).
DISPATCH_UNIT_PATH = os.environ.get("FOREMAN_DISPATCH_UNIT_PATH",
                                    "/home/lumen/bin:/usr/local/bin:/usr/bin:/bin")
GOVERNOR_STATE = os.environ.get("FOREMAN_STATE_JSON", os.path.join(REPO_ROOT, "state.json"))
GATE_RESETS_WITHIN_H = float(os.environ.get("FOREMAN_GATE_RESETS_WITHIN_H", "6"))
GATE_OK_TITLE = "[DISPATCH] window gate OK"
GATE_FAIL_TITLE = "[DISPATCH] window gate FAILED"


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="default", title=RECOVERED_TITLE):
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---- state.json: {"rung": N, "stopped": bool, "started_at": iso, "history": [...]} ---

def _fresh_state():
    return {"rung": 0, "stopped": False, "started_at": None, "history": []}


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
    """harness-preflight --all every migrated repo (fixes the stamp class, §1.4)."""
    cmd = [sys.executable, HARNESS_PREFLIGHT_CLI, "--all", "--claude-bin", CLAUDE_BIN]
    if dry_run:
        return True, "DRY-RUN would run: " + " ".join(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=580)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"harness-preflight --all failed to run: {e!r}"
    ok = p.returncode == 0
    tail = (p.stdout + p.stderr).strip()[-1500:]
    return ok, f"harness-preflight --all rc={p.returncode}\n{tail}"


def rung2_restart(dry_run):
    """restart the dispatch timer + start the service (fixes a wedged unit, §1.5)."""
    cmds = [["systemctl", "restart", DISPATCH_TIMER],
            ["systemctl", "start", DISPATCH_SERVICE]]
    if dry_run:
        return True, "DRY-RUN would run: " + " && ".join(" ".join(c) for c in cmds)
    details = []
    ok = True
    for c in cmds:
        try:
            p = subprocess.run(c, capture_output=True, text=True, timeout=60)
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


def _gate_check_stamps(dry_run):
    """§4 item 1: every migrated repo's preflight stamp fresh — reprove the stale ones
    NOW instead of leaving them for the next dispatcher pass's inline reprove (which
    only fires once a wave is already trying to dispatch, §1.3/§1.4). Cheap freshness
    check first (dispatch_gate_check reads a stamp file), live-proof reprove only for
    repos that actually need it — a live proof spawns a real throwaway claude session
    (harness_preflight.preflight's module docstring), not something to pay for on
    every migrated repo every night regardless of whether it's already proven."""
    sys.path.insert(0, HARNESS_DIR)
    sys.path.insert(0, ORCHESTRATOR_DIR)
    import harness_preflight   # noqa: E402
    import permission_profiles  # noqa: E402
    import router               # noqa: E402

    try:
        names = sorted(f[:-len(".json")] for f in os.listdir(permission_profiles.REPOS_DIR)
                        if f.endswith(".json"))
    except OSError as e:
        return False, f"cannot list {permission_profiles.REPOS_DIR}: {e!r}"
    by_name = {permission_profiles.repo_name(p): p for p in router.LANE_REPO.values()}
    repos = [by_name.get(name, f"/home/lumen/{name}") for name in names]

    stale = []
    for repo in repos:
        ok, reason = harness_preflight.dispatch_gate_check(repo)
        if not ok:
            stale.append((repo, reason))
    if not stale:
        return True, f"{len(repos)} migrated repo(s), stamps fresh"
    if dry_run:
        names_stale = [permission_profiles.repo_name(r) for r, _ in stale]
        return False, f"DRY-RUN would reprove {len(stale)} stale repo(s): {names_stale}"

    results = []
    all_ok = True
    for repo, _reason in stale:
        result = harness_preflight.preflight(repo, claude_bin=CLAUDE_BIN)
        all_ok = all_ok and result.ok
        results.append(f"{permission_profiles.repo_name(repo)}: "
                       f"{'ok' if result.ok else result.reason}")
    return all_ok, f"reproved {len(stale)}/{len(repos)} stale repo(s): " + "; ".join(results)


def _gate_check_dispatch(dry_run):
    """§4 item 2: foreman_dispatcher.py run --dry-run must show dispatchable>0 and
    would_spawn>0. On failure, name the top skip reasons instead of a bare
    'nothing would spawn' — the page should tell Frank where to look first."""
    cmd = [sys.executable, FOREMAN_DISPATCHER_CLI, "run", "--dry-run"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=580)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"dispatch --dry-run failed to run: {e!r}"

    events = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    pass_end = next((e for e in reversed(events) if e.get("event") == "pass_end"), None)
    if pass_end is None:
        tail = (p.stdout + p.stderr).strip()[-1500:]
        return False, f"dry-run produced no pass_end (rc={p.returncode})\n{tail}"

    dispatchable = pass_end.get("dispatchable", 0)
    would_spawn = pass_end.get("spawned", 0)  # --dry-run counts would_spawn into `spawned`
    if dispatchable > 0 and would_spawn > 0:
        return True, f"dispatchable={dispatchable} would_spawn={would_spawn}"

    reasons = Counter(e.get("reason") for e in events if e.get("event") == "skip")
    top = ", ".join(f"{r}={n}" for r, n in reasons.most_common(5)) or "none logged"
    return False, (f"dispatchable={dispatchable} would_spawn={would_spawn} — "
                   f"top skip reasons: {top}")


def _parse_iso(ts):
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _gate_check_governor():
    """§4 item 3a: governor state readable, with a limit resets_at inside the next
    FOREMAN_GATE_RESETS_WITHIN_H hours — the whole point of this gate is to run ahead
    of the Max reset, so a governor state with no reset landing soon means the gate
    is being asked the wrong week's question, not that anything is broken quietly."""
    if not os.path.exists(GOVERNOR_STATE):
        return False, f"governor state unreadable: {GOVERNOR_STATE} does not exist"
    try:
        with open(GOVERNOR_STATE) as f:
            state = json.load(f)
    except (OSError, ValueError) as e:
        return False, f"governor state unreadable: {e!r}"

    limits = state.get("limits") or {}
    now = _utcnow()
    within = []
    for kind, bucket in limits.items():
        rt = _parse_iso((bucket or {}).get("resets_at"))
        if rt is None:
            continue
        hours = (rt - now).total_seconds() / 3600
        if 0 <= hours <= GATE_RESETS_WITHIN_H:
            within.append(f"{kind} resets in {hours:.1f}h")
    if not within:
        return False, (f"no limit resets_at within {GATE_RESETS_WITHIN_H}h "
                       f"(limits tracked: {sorted(limits)})")
    return True, "; ".join(within)


def _gate_check_breaker_disarm():
    """§4 item 3b/3c: circuit breaker not tripped, DISPATCH_DISARM absent. Two
    independent checks kept separate — evaluate() reports the LIVE rate with no side
    effect, so it can be true ahead of the next real pass's check_and_trip() actually
    writing DISARM_PATH (circuit_breaker.py's module docstring); a stale DISARM left
    over from an unrelated manual stop is a different failure than an about-to-trip
    breaker, and the page should say which."""
    import circuit_breaker  # noqa: E402 — deferred: keeps `status`/`reset` callers light
    detail = []
    ok = True
    tripped, stats = circuit_breaker.evaluate()
    if tripped:
        ok = False
        detail.append(f"circuit breaker tripped: {stats['failures']}/{stats['total']} "
                      f"failed over {stats['window_minutes']}m")
    else:
        detail.append("circuit breaker not tripped")
    if os.path.exists(DISARM_PATH):
        ok = False
        detail.append(f"DISPATCH_DISARM present at {DISARM_PATH}")
    else:
        detail.append("DISPATCH_DISARM absent")
    return ok, "; ".join(detail)


def _gate_check_claude_auth():
    """§4 item 3d: `claude auth status` must succeed inside a transient unit carrying
    the dispatch service's own PATH (not this process's), via systemd-run --pipe — the
    same "works interactively, fails under the unit" class §1.3 documents for the bare
    `claude` name. CLAUDE_BIN stays absolute here on purpose (wq#1410 §2.2 item 1): this
    check is about the unit's *environment* passing auth, not about re-testing PATH
    resolution the absolute path already sidesteps."""
    cmd = ["systemd-run", "--pipe", "--collect", "--wait",
           f"--property=Environment=PATH={DISPATCH_UNIT_PATH}",
           CLAUDE_BIN, "auth", "status"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"claude auth status failed to run: {e!r}"
    tail = (p.stdout + p.stderr).strip()[-500:]
    return p.returncode == 0, f"claude auth status rc={p.returncode} {tail}"


GATE_CHECKS = (
    ("stamps_fresh", _gate_check_stamps),
    ("dispatch_dry_run", _gate_check_dispatch),
    ("governor_resets", lambda dry_run: _gate_check_governor()),
    ("breaker_disarm", lambda dry_run: _gate_check_breaker_disarm()),
    ("claude_auth", lambda dry_run: _gate_check_claude_auth()),
)


def _run_gate_checks(dry_run):
    return [(name, *fn(dry_run)) for name, fn in GATE_CHECKS]


def _format_gate_checks(results):
    return "\n".join(f"{'OK' if ok else 'FAIL'} {name}: {detail}"
                     for name, ok, detail in results)


def gate(dry_run=False):
    """The Thursday high-value-window pre-window gate, and (unmodified) the ordinary
    22:00 PT overnight gate — same function, two timers (§4): run while there is still
    time to fix things, not discover a stall at 03:00 with nobody watching.

    All five checks pass -> one default-priority 'window gate OK' ntfy, so silence is
    never ambiguous with "didn't run". Any failure -> advance the recovery ladder one
    rung (dispatch_recover's existing bounded ladder — never loops, never touches
    DISPATCH_DISARM) and recheck once; still failing -> urgent page naming exactly
    which check(s) are still red. stamps_fresh fixes itself inline (it reproves the
    stale repos as part of the check, §1.4) — the ladder is the fallback for the
    classes it can't reach: a wedged dispatch unit (rung 2) or unparseable config/
    profile JSON (rung 3)."""
    results = _run_gate_checks(dry_run)
    if all(ok for _, ok, _ in results):
        detail = _format_gate_checks(results)
        msg = "window gate OK:\n" + detail
        if dry_run:
            print("WOULD NTFY (gate OK):\n" + msg)
        else:
            _ntfy(msg, priority="default", title=GATE_OK_TITLE)
        return {"ok": True, "recovered": False, "checks": results, "detail": detail}

    recover_result = advance(dry_run=dry_run)
    recheck = _run_gate_checks(dry_run)
    recheck_detail = _format_gate_checks(recheck)

    if all(ok for _, ok, _ in recheck):
        msg = (f"window gate OK after recovery (ladder rung {recover_result.get('rung')}"
              f", {recover_result.get('action')}):\n{recheck_detail}")
        if dry_run:
            print("WOULD NTFY (gate OK after recovery):\n" + msg)
        else:
            _ntfy(msg, priority="default", title=GATE_OK_TITLE)
        return {"ok": True, "recovered": True, "recover": recover_result,
                "checks": recheck, "detail": recheck_detail}

    msg = (f"window gate FAILED, recovery did not fix it (ladder rung "
          f"{recover_result.get('rung')}, {recover_result.get('action')}):\n"
          f"{recheck_detail}")
    if dry_run:
        print("WOULD PAGE (gate failed):\n" + msg)
    else:
        _ntfy(msg, priority="urgent", title=GATE_FAIL_TITLE)
    return {"ok": False, "recovered": False, "recover": recover_result,
            "checks": recheck, "detail": recheck_detail}


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dispatcher stall recovery ladder (wq#1410)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("advance", help="run one rung forward if the ladder isn't stopped")
    a.add_argument("--dry-run", action="store_true")

    r = sub.add_parser("reset", help="reset the ladder (a healthy pass calls this)")
    r.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="print the current ladder state")
    sub.add_parser("selftest", help="synthetic stall -> classify -> dry-run rung 1")

    g = sub.add_parser("gate", help="wq#1411 high-value-window pre-flight gate")
    g.add_argument("--dry-run", action="store_true")

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
        result = gate(dry_run=args.dry_run)
        print(result["detail"])
        sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
