#!/usr/bin/env python3
"""Stop `wq release --dead-worker` from opening its own success output with a REFUSED banner
(wq#1539).

`_finish` (harness for `wq release`/`wq done`/etc.) calls `owner_ok()` first and only inspects
the wq#1498 `--dead-worker` override afterward. `owner_ok()` prints its own `REFUSED: wq#<id>
is held by ...` banner via `eprint` unconditionally on refusal, BEFORE `_finish` gets a chance
to evaluate the override. So a fully successful `wq release <id> --dead-worker` — holder
verified dead, claim released, logged on the record — opens its stderr with a REFUSED block
anyway, followed by the actual WARNING + released lines. A caller reading only the first
line, or a harness grepping stderr for REFUSED, concludes the call failed when it didn't.
First hit live in wq#1536 (first live use of wq#1498's release path).

The fix: `_finish` checks the wq#1498 `--dead-worker` override's own eligibility (holder set,
holder != caller, `holder_is_dead()`) BEFORE calling `owner_ok` at all, not after. When the
holder is verified dead, the override applies and `owner_ok` is never called — so it never
gets a chance to print. `owner_ok` itself is untouched: every other caller (a plain
release/done, `cmd_heartbeat`) still gets its refusal exactly as before, including the
`--dead-worker` case where the holder turns out to still be alive (that path still calls
`owner_ok` — same message as before, plus the override-specific REFUSED — no change there,
only the fully-successful override path changes).

Same keeper shape as ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py: `wq` is
rewritten in place by whichever lane touches it next, so the patch needs re-applying, not just
applying once. Anchors are taken from the wq#1498-patched wq (already live) since that patch
must be applied first.

    ensure_wq_release_dead_worker_order.py --check [--json]   is the live wq patched?
    ensure_wq_release_dead_worker_order.py --apply            patch it (backup, verify, roll back on failure)
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

FINISH_HEAD_OLD = '''    r = c.execute("SELECT claimed_by, subsystems, status FROM tasks WHERE id=?", (a.id,)).fetchone()
    if r is None:
        c.execute("COMMIT"); c.close(); eprint(f"REFUSED: no such task {a.id}"); sys.exit(1)
    caller, _ = resolve_identity(a)
    holder = r[0]
    if not owner_ok(c, caller, holder, new_status if new_status != 'open' else 'release', a.id):
        # wq#1498: `wq release --dead-worker` is the stale-claim path owner_ok's refusal text
        # now recommends INSTEAD of `wq retire` for a per-ticket fworker identity — retiring
        # that identity poisons every future spawn of the ticket, this does not. Only reachable
        # for release (new_status == 'open'), only when the caller declared it explicitly, and
        # only honored once holder_is_dead() has independently verified the holder is gone.
        dead_worker = new_status == 'open' and getattr(a, 'dead_worker', False)
        if dead_worker and holder and holder != caller and holder_is_dead(c, holder):
            warn(f"wq#{a.id}: holder '{holder}' verified dead (no live session"
                 + (", no running unit" if FWORKER_IDENTITY_RE.match(holder) else "")
                 + f") — releasing under --dead-worker on behalf of '{caller}' (wq#1498).")
            log_activity(c, caller, f"dead-worker-release:{holder}")
        elif dead_worker:
            c.execute("COMMIT"); c.close()
            eprint(f"REFUSED: wq#{a.id} held by '{holder}' — --dead-worker liveness check found "
                  f"it still alive (a live session row, or a running lumen-job-{holder}-* unit "
                  f"per `lumen-run list`). Not releasing.")
            sys.exit(1)
        else:
            c.execute("COMMIT"); c.close(); sys.exit(1)
'''

FINISH_HEAD_NEW = '''    r = c.execute("SELECT claimed_by, subsystems, status FROM tasks WHERE id=?", (a.id,)).fetchone()
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
        if dead_worker:
            c.execute("COMMIT"); c.close()
            eprint(f"REFUSED: wq#{a.id} held by '{holder}' — --dead-worker liveness check found "
                  f"it still alive (a live session row, or a running lumen-job-{holder}-* unit "
                  f"per `lumen-run list`). Not releasing.")
            sys.exit(1)
        else:
            c.execute("COMMIT"); c.close(); sys.exit(1)
'''

# (name, old, new, expected occurrences). Each `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched. The anchor is the wq#1498-patched _finish head (already live)
# since that patch must land first — this one builds directly on top of it. `owner_ok` itself
# is deliberately left untouched (no signature/body change) so ops/wq1498's and
# ops/wq1479's own keepers, which anchor inside owner_ok, stay valid.
PATCHES = [
    ("finish-checks-override-before-owner_ok", FINISH_HEAD_OLD, FINISH_HEAD_NEW, 1),
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
    """Prove the candidate on a scratch queue, against a stub `lumen-run`: a successful
    `release --dead-worker` has NO 'REFUSED' anywhere on stderr (wq#1539's actual bug); a
    plain (non-dead-worker) owner conflict still prints the REFUSED banner exactly as before;
    a `--dead-worker` call against a still-live holder still refuses (owner_ok is reached
    since the override isn't eligible) with the liveness-specific message too."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq1539-verify")
    fake_bin = os.path.join(d, "fake-lumen-run")
    with open(fake_bin, "w", encoding="utf-8") as f:
        f.write(FAKE_LUMEN_RUN)
    os.chmod(fake_bin, 0o755)

    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2144/router.py"  # wq#2139 lane guard fails open
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
        p = wq("add", "--lane", "auto", "--title", title, agent="wq1539-verify")
        if p.returncode != 0 or not p.stdout.startswith("added"):
            raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
        return p.stdout.split()[2]

    reg = wq("whoami", "--register", "orch-wq1539-verify", agent=None)
    orch_sid = next((l.split(": ", 1)[1] for l in reg.stdout.splitlines()
                     if l.startswith("REGISTERED session: ")), None)
    if not orch_sid:
        raise RuntimeError(f"scratch orchestrator registration failed: {reg.stdout}{reg.stderr}")

    # 1. still-alive holder: the override isn't eligible (holder_is_dead() says no), so
    #    owner_ok IS reached and refuses — this path is unaffected by wq#1539.
    tid = add("wq1539 verify: dead-worker refused, still alive")
    holder = f"fworker-wq{tid}"
    p = wq("claim", holder, "--id", tid)
    if f"CLAIMED {tid}" not in p.stdout:
        raise RuntimeError(f"scratch claim failed: {p.stdout}{p.stderr}")
    p = wq("release", tid, "--dead-worker", agent=orch_sid, extra_env={"FAKE_RUNNING_UNITS": holder})
    if p.returncode == 0:
        raise RuntimeError(f"release --dead-worker on a live holder should refuse: {p.stdout}{p.stderr}")
    if "REFUSED" not in p.stderr or "liveness check found it still alive" not in p.stderr:
        raise RuntimeError(f"expected the liveness-specific REFUSED on stderr, got: {p.stderr!r}")

    # 2. dead holder: release succeeds, and stderr carries NO 'REFUSED' anywhere (wq#1539).
    tid2 = add("wq1539 verify: dead-worker release is clean")
    holder2 = f"fworker-wq{tid2}"
    p = wq("claim", holder2, "--id", tid2)
    if f"CLAIMED {tid2}" not in p.stdout:
        raise RuntimeError(f"scratch claim (2) failed: {p.stdout}{p.stderr}")
    p = wq("release", tid2, "--dead-worker", agent=orch_sid, extra_env={"FAKE_RUNNING_UNITS": ""})
    if p.returncode != 0:
        raise RuntimeError(f"release --dead-worker on a verified-dead holder should succeed: "
                           f"{p.stdout}{p.stderr}")
    if "REFUSED" in p.stderr:
        raise RuntimeError(f"a successful dead-worker release must not print REFUSED: {p.stderr!r}")

    # 3. an ordinary (non-dead-worker) owner conflict is unaffected: the generic banner still
    #    prints exactly as before.
    tid3 = add("wq1539 verify: ordinary owner conflict unaffected")
    holder3 = f"fworker-wq{tid3}"
    p = wq("claim", holder3, "--id", tid3)
    if f"CLAIMED {tid3}" not in p.stdout:
        raise RuntimeError(f"scratch claim (3) failed: {p.stdout}{p.stderr}")
    p = wq("release", tid3, agent=orch_sid)
    if p.returncode == 0:
        raise RuntimeError(f"plain release of someone else's claim should refuse: {p.stdout}{p.stderr}")
    if f"REFUSED: wq#{tid3} is held by '{holder3}'" not in p.stderr:
        raise RuntimeError(f"an ordinary owner conflict must still print its REFUSED banner: {p.stderr!r}")


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

    backup = f"{a.wq}.bak.wq1539.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq1539-cand"), "wq")
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
