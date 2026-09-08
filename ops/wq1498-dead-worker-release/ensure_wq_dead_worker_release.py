#!/usr/bin/env python3
"""Keep `wq` from letting `retire` poison a per-ticket fworker identity (wq#1498).

`fworker.wq_identity(ticket_id)` (harness/fworker.py) mints the STABLE name `fworker-wq<id>`
that every attempt at a ticket claims under — it is never a dead session, only ever a
currently-idle one. Before this patch, a stale claim under that name left an operator with
only one documented remedy: owner_ok's own refusal text said `wq retire fworker-wq<id>`. That
plants a permanent `retired_sessions` row, so the NEXT fworker spawned for the same ticket
claims under the identical name and hits the STOP banner. Two orchestrator sessions
(orch-ticket-reconcile, orch-fleetops-broker) hit this within 3 minutes of each other on
2026-09-08 (RETIRED-REFUSED rows for fworker-wq1278/1345/1387, 13 tickets); the rows were
deleted by hand as a bandaid.

Three changes, all scoped to the shape `^fworker-wq\\d+$` (env-overridable via
WQ_FWORKER_IDENTITY_RE so tests can probe the boundary without hardcoding it twice):
  1. `wq retire fworker-wq<id>` now refuses unless `--dead-worker` is also passed — the flag
     is the explicit "I really mean to permanently kill this per-ticket identity" confirmation.
  2. `owner_ok`'s refusal text stops recommending retire for that shape and points at
     `wq release <id> --dead-worker` instead.
  3. `wq release <id> --dead-worker` is the real stale-claim path: it only overrides owner_ok's
     refusal once `holder_is_dead()` independently verifies the holder — no live
     `agent_sessions` row, and (for the fworker-wq<id> shape) no running
     `lumen-job-fworker-wq<id>-*` unit per `lumen-run list` (same signal
     harness/concurrency.py's own concurrency cap reads) — and it logs the holder it released
     to `agent_activity` on the record.

Same keeper shape as ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py: `wq` is rewritten in
place by whichever lane touches it next, so the patch needs re-applying, not just applying
once.

    ensure_wq_dead_worker_release.py --check [--json]   is the live wq patched?
    ensure_wq_dead_worker_release.py --apply            patch it (backup, verify, roll back on failure)
"""
import argparse
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

HOLDER_LIVENESS_HELPERS = r'''def holder_unit_running(name):
    """wq#1498: is a live `lumen-job-<name>-<ts>.service` unit running right now, per
    `lumen-run list`? Fails CLOSED (assume running) on any lumen-run trouble — a liveness
    check that can't reach lumen-run must never be read as 'confirmed dead'."""
    try:
        import subprocess
        p = subprocess.run([LUMEN_RUN_BIN, "list"], capture_output=True, text=True, timeout=15)
    except Exception:
        return True
    if p.returncode != 0:
        return True
    for line in p.stdout.splitlines():
        if "running" not in line:
            continue
        m = LUMEN_RUN_UNIT_RE.search(line)
        if m and m.group("name") == name:
            return True
    return False

def holder_is_dead(c, holder):
    """wq#1498: the liveness check `wq release --dead-worker` uses instead of `wq retire` on a
    per-ticket fworker identity. Dead means BOTH: no live agent_sessions row (fworker never
    registers, so this alone can't distinguish idle-between-attempts from actually-dead), AND —
    when the holder has the fworker-wq<id> shape — no running lumen-job-<holder>-<ts> unit per
    `lumen-run list`. A holder that isn't the per-ticket shape has no unit-name signal to check,
    so the session-row check is all that applies to it."""
    if is_session(c, holder):
        return False
    if FWORKER_IDENTITY_RE.match(holder or "") and holder_unit_running(holder):
        return False
    return True

def mark_retired(c, sid, base, kind, reason):
'''

OWNER_OK_TAIL_NEW = r'''    why = ("A registered session always knows its identity and has no excuse."
           if caller_is_session else
           "wq#1116: an unregistered caller may no longer write to another identity's LIVE claim.")
    # wq#1498: fworker-wq<id> is the STABLE per-ticket identity, never a dead session — retiring
    # it (the old advice below) poisons every future spawn of that ticket. Point at the release
    # path instead: it verifies liveness and clears the claim without planting that marker.
    if FWORKER_IDENTITY_RE.match(claimed_by or ""):
        stale_hint = (f"  `wq release {tid} --dead-worker` releases it on the record once "
                      f"liveness is verified (no live session, no running unit) — do NOT\n"
                      f"  `wq retire {claimed_by}`: that is a per-ticket identity, not a dead "
                      f"session, and retiring it refuses every future spawn of wq#{tid} too "
                      f"(wq#1498).")
    else:
        stale_hint = (f"  `wq retire {claimed_by}` to revert its claims and drop its slocks on "
                      f"the record.")
    eprint(f"REFUSED: wq#{tid} is held by '{claimed_by}', you are '{caller}'.\n"
          f"  An agent may never {action} another session's claim. {why}\n"
          f"  If that session is dead, let its lease expire (auto-reverts to open), or\n"
          f"{stale_hint}\n"
          f"  If you ARE that worker: register first — wq whoami --register <your-agent-name>\n"
          f"  — so your identity survives across wq invocations instead of changing every call.")
    return False
'''

OWNER_OK_TAIL_OLD = r'''    why = ("A registered session always knows its identity and has no excuse."
           if caller_is_session else
           "wq#1116: an unregistered caller may no longer write to another identity's LIVE claim.")
    eprint(f"REFUSED: wq#{tid} is held by '{claimed_by}', you are '{caller}'.\n"
          f"  An agent may never {action} another session's claim. {why}\n"
          f"  If that session is dead, let its lease expire (auto-reverts to open), or\n"
          f"  `wq retire {claimed_by}` to revert its claims and drop its slocks on the record.\n"
          f"  If you ARE that worker: register first — wq whoami --register <your-agent-name>\n"
          f"  — so your identity survives across wq invocations instead of changing every call.")
    return False
'''

FINISH_HEAD_NEW = r'''    r = c.execute("SELECT claimed_by, subsystems, status FROM tasks WHERE id=?", (a.id,)).fetchone()
    if r is None:
        c.execute("COMMIT"); c.close(); eprint(f"REFUSED: no such task {a.id}"); sys.exit(1)
    caller, _ = resolve_identity(a)
    holder = r[0]
    # wq#1539: the --dead-worker override's OWN eligibility (holder set, holder != caller,
    # holder_is_dead()) is checked BEFORE owner_ok is called at all — was after. owner_ok
    # eprints its REFUSED banner unconditionally on refusal, so calling it first made every
    # successful `release --dead-worker` open its stderr with a false-looking REFUSED line
    # ahead of the WARNING + released lines. Calling owner_ok only when the override does NOT
    # apply means a verified-dead holder never triggers that banner at all (wq#1539).
    dead_worker = new_status == 'open' and getattr(a, 'dead_worker', False)
    if dead_worker and holder and holder != caller and holder_is_dead(c, holder):
        # wq#1498: `wq release --dead-worker` is the stale-claim path owner_ok's refusal text
        # recommends INSTEAD of `wq retire` for a per-ticket fworker identity — retiring that
        # identity poisons every future spawn of the ticket, this does not.
        warn(f"wq#{a.id}: holder '{holder}' verified dead (no live session"
             + (", no running unit" if FWORKER_IDENTITY_RE.match(holder) else "")
             + f") — releasing under --dead-worker on behalf of '{caller}' (wq#1498).")
        log_activity(c, caller, f"dead-worker-release:{holder}")
    elif not owner_ok(c, caller, holder, new_status if new_status != 'open' else 'release', a.id):
        if dead_worker:
            c.execute("COMMIT"); c.close()
            eprint(f"REFUSED: wq#{a.id} held by '{holder}' — --dead-worker liveness check found "
                  f"it still alive (a live session row, or a running lumen-job-{holder}-* unit "
                  f"per `lumen-run list`). Not releasing.")
            sys.exit(1)
        else:
            c.execute("COMMIT"); c.close(); sys.exit(1)
'''

FINISH_HEAD_OLD = r'''    r = c.execute("SELECT claimed_by, subsystems, status FROM tasks WHERE id=?", (a.id,)).fetchone()
    if r is None:
        c.execute("COMMIT"); c.close(); eprint(f"REFUSED: no such task {a.id}"); sys.exit(1)
    caller, _ = resolve_identity(a)
    if not owner_ok(c, caller, r[0], new_status if new_status != 'open' else 'release', a.id):
        c.execute("COMMIT"); c.close(); sys.exit(1)
'''

RETIRE_HEAD_NEW = r'''    target = a.target.strip()
    reason = (a.reason or "retired by operator").strip()
    if not target:
        print("REFUSED: retire needs a session id or base name"); sys.exit(1)
    # wq#1498: fworker-wq<id> is fworker.wq_identity(ticket_id) — the STABLE name every future
    # attempt at this ticket claims under, never a dead session. Retiring it plants a permanent
    # retired_sessions row that STOP-banners every future spawn of the same ticket. This is
    # exactly the shape that made two orchestrator sessions hand-delete retired_sessions rows on
    # 2026-09-08 after following owner_ok's own (now-fixed) advice to retire it.
    if FWORKER_IDENTITY_RE.match(target) and not getattr(a, "dead_worker", False):
        print(f"REFUSED: '{target}' is a per-ticket fworker identity (fworker.wq_identity()), "
              f"not a dead session — retiring it would refuse every FUTURE spawn of this ticket "
              f"too (wq#1498). For a stale claim under this name, use "
              f"`wq release <ticket-id> --dead-worker` instead: it verifies liveness (no live "
              f"session, no running unit) and releases the claim on the record without "
              f"poisoning the identity. Pass --dead-worker here only if you really mean to "
              f"permanently retire this identity.")
        sys.exit(1)
    c = conn(); c.isolation_level = None
'''

RETIRE_HEAD_OLD = r'''    target = a.target.strip()
    reason = (a.reason or "retired by operator").strip()
    if not target:
        print("REFUSED: retire needs a session id or base name"); sys.exit(1)
    c = conn(); c.isolation_level = None
'''

# (name, old, new, expected occurrences). Each `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched. Anchors are taken from the wq#1479-patched wq (owner_ok /
# _finish refusals already on stderr via eprint) since that patch is already live.
PATCHES = [
    ("identity-constants",
     'NTFY_BIN = os.environ.get("WQ_NTFY_BIN", "/home/lumen/bin/ntfy-send")\n'
     'SESSION_RE = re.compile(r"^(?P<base>.+)-(?P<sid>[0-9a-f]{6})$")\n',
     'NTFY_BIN = os.environ.get("WQ_NTFY_BIN", "/home/lumen/bin/ntfy-send")\n'
     '# wq#1498: fworker.wq_identity(ticket_id) mints this STABLE per-ticket name (harness/fworker.py)\n'
     '# and claims under it on every attempt for that ticket — it is never a dead session, only ever a\n'
     '# currently-idle one. `wq retire` on it plants a permanent retired_sessions row that refuses\n'
     '# every FUTURE spawn for the same ticket too. Env-overridable so tests can probe the boundary\n'
     '# without hardcoding the shape twice.\n'
     'FWORKER_IDENTITY_RE = re.compile(\n'
     '    os.environ.get("WQ_FWORKER_IDENTITY_RE", r"^fworker-wq\\d+$"))\n'
     '# wq#1498: `lumen-run list` unit column, e.g. "lumen-job-fworker-wq616-1786850106.service" —\n'
     "# mirrors harness/concurrency.py's UNIT_RE so a --dead-worker liveness check reads the exact\n"
     '# same signal the dispatcher\'s own concurrency cap does, instead of inventing a second one.\n'
     'LUMEN_RUN_UNIT_RE = re.compile(r"lumen-job-(?P<name>\\S+?)-\\d+\\.service")\n'
     'LUMEN_RUN_BIN = os.environ.get("WQ_LUMEN_RUN_BIN", "/home/lumen/bin/lumen-run")\n'
     'SESSION_RE = re.compile(r"^(?P<base>.+)-(?P<sid>[0-9a-f]{6})$")\n',
     1),
    ("owner_ok-stale-hint", OWNER_OK_TAIL_OLD, OWNER_OK_TAIL_NEW, 1),
    ("holder-liveness-helpers", "def mark_retired(c, sid, base, kind, reason):\n",
     HOLDER_LIVENESS_HELPERS, 1),
    ("finish-dead-worker-override", FINISH_HEAD_OLD, FINISH_HEAD_NEW, 1),
    ("release-dead-worker-flag",
     's=sub.add_parser("release"); s.add_argument("id",type=int); '
     's.add_argument("--agent",default=None); '
     's.add_argument("--fence",type=int,default=None,help=FENCE_HELP); '
     's.set_defaults(f=cmd_release)\n',
     's=sub.add_parser("release"); s.add_argument("id",type=int); '
     's.add_argument("--agent",default=None); '
     's.add_argument("--fence",type=int,default=None,help=FENCE_HELP); '
     's.add_argument("--dead-worker",dest="dead_worker",action="store_true",'
     'help="wq#1498: release a claim held by a DIFFERENT identity if, and only if, it '
     'verifiably has no live session and (for fworker-wq<id>) no running unit — the '
     'stale-claim path that does not poison a per-ticket identity the way `wq retire` does"); '
     's.set_defaults(f=cmd_release)\n',
     1),
    ("retire-refuses-fworker-identity", RETIRE_HEAD_OLD, RETIRE_HEAD_NEW, 1),
    ("retire-dead-worker-flag",
     's=sub.add_parser("retire",help="wq#568: retire a session id or base name — releases its '
     'claims/slocks and refuses all its future wq/slock calls"); s.add_argument("target"); '
     's.add_argument("--reason",default=None); s.set_defaults(f=cmd_retire)\n',
     's=sub.add_parser("retire",help="wq#568: retire a session id or base name — releases its '
     'claims/slocks and refuses all its future wq/slock calls"); s.add_argument("target"); '
     's.add_argument("--reason",default=None); '
     's.add_argument("--dead-worker",dest="dead_worker",action="store_true",'
     'help="wq#1498: required to retire a fworker-wq<id> per-ticket identity — confirms you '
     'mean to permanently poison future spawns of that ticket, not just clear a stale claim '
     '(use `wq release --dead-worker` for that instead)"); s.set_defaults(f=cmd_retire)\n',
     1),
]

FAKE_LUMEN_RUN = '''#!/usr/bin/env python3
import os, sys
if len(sys.argv) > 1 and sys.argv[1] == "list":
    for name in [n for n in os.environ.get("FAKE_RUNNING_UNITS", "").split(",") if n]:
        print(f"STUB   lumen-job-{name}-1786850106.service   running")
sys.exit(0)
'''


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def status(src):
    """Per-patch: applied / pending / MISSING-ANCHOR (wq moved and the old text is gone)."""
    out = {}
    for name, old, new, n in PATCHES:
        if src.count(new) >= n:
            out[name] = "applied"
        elif src.count(old) == n:
            out[name] = "pending"
        else:
            out[name] = f"MISSING-ANCHOR (old={src.count(old)} new={src.count(new)} want={n})"
    return out


def apply_to(src):
    for name, old, new, n in PATCHES:
        if src.count(new) >= n:
            continue
        if src.count(old) != n:
            raise RuntimeError(f"anchor for {name!r} matched {src.count(old)} times, want {n} "
                               f"— wq has moved; re-derive the patch before applying")
        src = src.replace(old, new)
    return src


def verify(candidate):
    """Prove the candidate on a scratch queue, against a stub `lumen-run`, before it goes
    anywhere near the live binary: retire on a per-ticket identity refuses, --dead-worker
    escapes that refusal, and `release --dead-worker` only succeeds once the stub reports the
    holder's unit is not running."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq1498-verify")
    fake_bin = os.path.join(d, "fake-lumen-run")
    with open(fake_bin, "w", encoding="utf-8") as f:
        f.write(FAKE_LUMEN_RUN)
    os.chmod(fake_bin, 0o755)

    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_LUMEN_RUN_BIN"] = fake_bin
    env.setdefault("FAKE_RUNNING_UNITS", "")

    def wq(*args, agent=None):
        e = dict(env)
        if agent is not None:
            e["WQ_AGENT"] = agent
        else:
            e.pop("WQ_AGENT", None)
        return subprocess.run([sys.executable, candidate, *args],
                              capture_output=True, text=True, env=e)

    def add(title):
        p = wq("add", "--lane", "auto", "--title", title, agent="wq1498-verify")
        if p.returncode != 0 or not p.stdout.startswith("added"):
            raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
        return p.stdout.split()[2]

    # 1. retire on the per-ticket identity refuses without --dead-worker, claim untouched.
    tid = add("wq1498 verify: retire refusal")
    holder = f"fworker-wq{tid}"
    p = wq("claim", holder, "--id", tid)
    if f"CLAIMED {tid}" not in p.stdout:
        raise RuntimeError(f"scratch claim failed: {p.stdout}{p.stderr}")
    p = wq("retire", holder)
    if p.returncode == 0:
        raise RuntimeError(f"retire {holder} should have refused: {p.stdout}{p.stderr}")
    if "wq#1498" not in p.stdout:
        raise RuntimeError(f"retire refusal is missing its wq#1498 explanation: {p.stdout!r}")
    c = sqlite3.connect(env["WQ_DB"])
    st = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.close()
    if st != "claimed":
        raise RuntimeError(f"a refused retire must not touch the claim, got status={st!r}")

    # 2. --dead-worker release only succeeds once the stub reports the unit not running, and
    #    it logs the holder to agent_activity.
    tid2 = add("wq1498 verify: dead-worker release")
    holder2 = f"fworker-wq{tid2}"
    p = wq("claim", holder2, "--id", tid2)
    if f"CLAIMED {tid2}" not in p.stdout:
        raise RuntimeError(f"scratch claim (2) failed: {p.stdout}{p.stderr}")
    reg = wq("whoami", "--register", "orch-wq1498-verify", agent=None)
    orch_sid = next((l.split(": ", 1)[1] for l in reg.stdout.splitlines()
                     if l.startswith("REGISTERED session: ")), None)
    if not orch_sid:
        raise RuntimeError(f"scratch orchestrator registration failed: {reg.stdout}{reg.stderr}")

    still_running_env = dict(env); still_running_env["FAKE_RUNNING_UNITS"] = holder2
    p = subprocess.run([sys.executable, candidate, "release", tid2, "--dead-worker"],
                       capture_output=True, text=True,
                       env={**still_running_env, "WQ_AGENT": orch_sid})
    if p.returncode == 0:
        raise RuntimeError(f"release --dead-worker must refuse a still-running unit: "
                           f"{p.stdout}{p.stderr}")

    dead_env = dict(env); dead_env["FAKE_RUNNING_UNITS"] = ""
    p = subprocess.run([sys.executable, candidate, "release", tid2, "--dead-worker"],
                       capture_output=True, text=True, env={**dead_env, "WQ_AGENT": orch_sid})
    if p.returncode != 0:
        raise RuntimeError(f"release --dead-worker on a verified-dead holder should succeed: "
                           f"{p.stdout}{p.stderr}")
    c = sqlite3.connect(env["WQ_DB"])
    st2, cb2 = c.execute("SELECT status, claimed_by FROM tasks WHERE id=?", (tid2,)).fetchone()
    logged = c.execute("SELECT cmd FROM agent_activity WHERE session_id=?",
                       (orch_sid,)).fetchall()
    c.close()
    if (st2, cb2) != ("open", None):
        raise RuntimeError(f"dead-worker release should reopen the ticket, got {(st2, cb2)!r}")
    if not any(row[0].startswith(f"dead-worker-release:{holder2}") for row in logged):
        raise RuntimeError(f"dead-worker release did not log the holder it released: {logged!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report whether the live wq is patched")
    ap.add_argument("--apply", action="store_true", help="patch the live wq (idempotent)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--wq", default=LIVE_WQ)
    a = ap.parse_args()
    if not (a.check or a.apply):
        ap.error("one of --check / --apply")

    src = read(a.wq)
    st = status(src)
    ok = all(v == "applied" for v in st.values())

    if a.check or not a.apply:
        report = {"wq": a.wq, "patched": ok, "patches": st}
        print(json.dumps(report, indent=2) if a.json else
              "\n".join([f"{a.wq}: {'PATCHED' if ok else 'NOT PATCHED'}"] +
                        [f"  {k:32} {v}" for k, v in st.items()]))
        if not a.apply:
            return 0 if ok else 1

    if ok:
        print("already applied — nothing to do")
        return 0

    backup = f"{a.wq}.bak.wq1498.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq1498-cand"), "wq")
    with open(cand, "w", encoding="utf-8") as f:
        f.write(apply_to(src))
    shutil.copymode(a.wq, cand)
    try:
        verify(cand)
    except Exception as e:
        print(f"REFUSING to apply — the candidate failed verification: {e}", file=sys.stderr)
        return 2
    shutil.copyfile(cand, a.wq)
    try:
        verify(a.wq)
    except Exception as e:
        shutil.copyfile(backup, a.wq)
        print(f"applied then ROLLED BACK from {backup}: {e}", file=sys.stderr)
        return 2
    print(f"applied to {a.wq} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
