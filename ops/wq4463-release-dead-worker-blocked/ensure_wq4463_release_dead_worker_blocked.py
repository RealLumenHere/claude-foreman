#!/usr/bin/env python3
"""`wq release --dead-worker` on a BLOCKED ticket flipped it to open and wiped
block_reason (wq#4463).

Observed 2026-10-21 on ticket 2842: blocked, block_reason 1359 chars. After
`wq release 2842 --dead-worker`, the ticket read status open, block_reason NULL.
`_finish`'s wq#1498/wq#1539 override only cleared the dead holder's claim and then
fell through into the generic `new_status == 'open'` UPDATE, which unconditionally
sets status='open' and block_reason=NULL/blocked_at=NULL. At `open` the ~20min
foreman dispatcher can redispatch a ticket someone deliberately blocked (often
Frank's own call) within one dispatch cycle. 26 blocked tickets carried a dead
fworker claimed_by at the time this was found, so the footgun was live, not
theoretical.

The fix: inside the wq#1498/wq#1539 dead-worker-override branch, when the ticket's
CURRENT status is 'blocked', clear claimed_by/lease/hold and release subsystem
locks directly, then return 0 before the generic UPDATE ever runs. status and
block_reason/blocked_at are left untouched. A non-blocked holder (open/claimed)
is unaffected and falls through to the pre-existing generic release path exactly
as before.

Same keeper shape as ops/wq1539-release-dead-worker-order/ensure_wq_release_dead_worker_order.py:
`wq` is a live file that any lane may rewrite next, so the patch needs
re-applying, not just applying once. The anchor is taken from the wq#1539-patched
`_finish` (already live) since that patch landed first.

    ensure_wq4463_release_dead_worker_blocked.py --check [--json]   is the live wq patched?
    ensure_wq4463_release_dead_worker_blocked.py --apply            patch it (backup, verify, roll back on failure)
"""
import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time

LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

FINISH_OLD = '''        log_activity(c, caller, f"dead-worker-release:{holder}")
    elif not owner_ok(c, caller, holder, new_status if new_status != 'open' else 'release', a.id):
'''

FINISH_NEW = '''        log_activity(c, caller, f"dead-worker-release:{holder}")
        # wq#4463: status/block_reason on a blocked ticket is a deliberate decision (often
        # Frank's) the ~20min dispatcher must keep honoring. --dead-worker clears a dead
        # holder's stale claim; it must never also silently unblock the ticket, or the
        # dispatcher redispatches a ticket someone deliberately blocked. Clear claimed_by
        # (and lease/hold) only; status and block_reason/blocked_at are left untouched.
        if r[2] == 'blocked':
            fcl, fpar = fence_guard(c, a, a.id, "release")
            mark_alive(c, caller, "release")
            if r[1]:
                release_subs(c, parse_subs(r[1]), r[0] or "")
            clr = lease_clear_sql(c)
            cur = c.execute(f"UPDATE tasks SET claimed_by=NULL, claimed_at=NULL, "
                            f"lease_expires=NULL, lease_ttl=NULL, hold_by=NULL, "
                            f"hold_expires=NULL{clr} WHERE id=?{fcl}", [a.id] + fpar)
            if cur.rowcount == 0:
                c.execute("ROLLBACK"); c.close()
                eprint(f"REFUSED: release on wq#{a.id} matched no row under fence "
                      f"{getattr(a,'fence',None)} — the claim moved while this call was in "
                      f"flight (wq#1158)."); sys.exit(5)
            c.execute("COMMIT"); c.close()
            warn(f"wq#{a.id}: stayed blocked — --dead-worker clears a stale claim, it does "
                 f"not unblock (wq#4463). block_reason preserved.")
            return 0
    elif not owner_ok(c, caller, holder, new_status if new_status != 'open' else 'release', a.id):
'''

# (name, old, new, expected occurrences). `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched.
PATCHES = [
    ("finish-blocked-holder-stays-blocked-under-dead-worker", FINISH_OLD, FINISH_NEW, 1),
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
    """Prove the candidate on a scratch queue, against a stub `lumen-run`:
    1. a dead-worker release of a BLOCKED ticket stays blocked, keeps block_reason, clears
       claimed_by (wq#4463's actual bug).
    2. a dead-worker release of an ordinary open/claimed ticket is unaffected: it still
       flips to open exactly as before.
    3. a still-alive holder is still refused (wq#1539 unaffected)."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq4463-verify")
    fake_bin = os.path.join(d, "fake-lumen-run")
    with open(fake_bin, "w", encoding="utf-8") as f:
        f.write(FAKE_LUMEN_RUN)
    os.chmod(fake_bin, 0o755)

    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2144/router.py"
    env["WQ_LUMEN_RUN_BIN"] = fake_bin
    env.setdefault("FAKE_RUNNING_UNITS", "")

    def wq(*args, agent=None, extra_env=None):
        e = dict(env)
        if extra_env:
            e.update(extra_env)
        if agent is not None:
            e["WQ_AGENT"] = agent
        else:
            e.pop("WQ_AGENT", None)
        return subprocess.run([sys.executable, candidate, *args],
                              capture_output=True, text=True, env=e)

    def add(title):
        p = wq("add", "--lane", "auto", "--title", title, agent="wq4463-verify")
        if p.returncode != 0 or not p.stdout.startswith("added"):
            raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
        return p.stdout.split()[2]

    reg = wq("whoami", "--register", "orch-wq4463-verify", agent=None)
    orch_sid = next((l.split(": ", 1)[1] for l in reg.stdout.splitlines()
                     if l.startswith("REGISTERED session: ")), None)
    if not orch_sid:
        raise RuntimeError(f"scratch orchestrator registration failed: {reg.stdout}{reg.stderr}")

    # 1. blocked ticket, dead holder: release must NOT unblock, must clear claimed_by.
    tid = add("wq4463 verify: blocked ticket stays blocked")
    holder = f"fworker-wq{tid}"
    p = wq("claim", holder, "--id", tid)
    if f"CLAIMED {tid}" not in p.stdout:
        raise RuntimeError(f"scratch claim failed: {p.stdout}{p.stderr}")
    p = wq("block", tid, "deliberately parked for wq4463 verify", agent=holder)
    if p.returncode != 0:
        raise RuntimeError(f"scratch block failed: {p.stdout}{p.stderr}")
    p = wq("release", tid, "--dead-worker", agent=orch_sid, extra_env={"FAKE_RUNNING_UNITS": ""})
    if p.returncode != 0:
        raise RuntimeError(f"dead-worker release of a blocked ticket should succeed: {p.stdout}{p.stderr}")
    if "stayed blocked" not in p.stderr:
        raise RuntimeError(f"expected the wq#4463 'stayed blocked' notice: {p.stderr!r}")
    p2 = wq("show", tid, "--json")
    row = json.loads(p2.stdout)
    if row.get("status") != "blocked":
        raise RuntimeError(f"status must stay 'blocked', got {row.get('status')!r}")
    if not row.get("block_reason"):
        raise RuntimeError("block_reason must be preserved, got empty/NULL")
    if row.get("claimed_by"):
        raise RuntimeError(f"claimed_by must be cleared, got {row.get('claimed_by')!r}")

    # 2. open/claimed ticket, dead holder: unaffected, still flips to open.
    tid2 = add("wq4463 verify: ordinary dead-worker release unaffected")
    holder2 = f"fworker-wq{tid2}"
    p = wq("claim", holder2, "--id", tid2)
    if f"CLAIMED {tid2}" not in p.stdout:
        raise RuntimeError(f"scratch claim (2) failed: {p.stdout}{p.stderr}")
    p = wq("release", tid2, "--dead-worker", agent=orch_sid, extra_env={"FAKE_RUNNING_UNITS": ""})
    if p.returncode != 0:
        raise RuntimeError(f"dead-worker release of an open/claimed ticket should succeed: {p.stdout}{p.stderr}")
    p2 = wq("show", tid2, "--json")
    row2 = json.loads(p2.stdout)
    if row2.get("status") != "open" or row2.get("claimed_by"):
        raise RuntimeError(f"ordinary release must still flip to open/unclaimed, got {row2}")

    # 3. still-alive holder is still refused (wq#1539 unaffected).
    tid3 = add("wq4463 verify: still-alive holder still refused")
    holder3 = f"fworker-wq{tid3}"
    p = wq("claim", holder3, "--id", tid3)
    if f"CLAIMED {tid3}" not in p.stdout:
        raise RuntimeError(f"scratch claim (3) failed: {p.stdout}{p.stderr}")
    p = wq("release", tid3, "--dead-worker", agent=orch_sid, extra_env={"FAKE_RUNNING_UNITS": holder3})
    if p.returncode == 0:
        raise RuntimeError(f"release --dead-worker on a live holder should refuse: {p.stdout}{p.stderr}")
    if "liveness check found it still alive" not in p.stderr:
        raise RuntimeError(f"expected the liveness-specific REFUSED on stderr, got: {p.stderr!r}")


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
                        [f"  {k:50} {v}" for k, v in st.items()]))
        if not a.apply:
            return 0 if ok else 1

    if ok:
        print("already applied — nothing to do")
        return 0

    backup = f"{a.wq}.bak.wq4463.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq4463-cand"), "wq")
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
