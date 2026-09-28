#!/usr/bin/env python3
"""Make `wq done` agree with `wq claim` on the 'user' lane rule (wq#2997).

`cmd_claim` refuses the 'user' lane unconditionally (line ~1559: "'user' lane is Frank-only —
never agent-claimable") and so does `wq claim --id` (line ~1593) and `wq hold` (line ~3078).
`cmd_done` had no matching check. `owner_ok()` short-circuits `True` whenever `claimed_by` is
falsy (`if not claimed_by or caller == claimed_by: return True`) — and a 'user'-lane ticket can
never carry a live claim, since `claim` always refuses it — so `_finish()`'s ownership gate was
never in a position to catch this. On 2026-09-28, `wq claim --id 1304` correctly refused (user
lane), but `wq done 1304 --no-artifact ...` closed it clean with no claim ever made. Frank had
directed that specific close in chat, so the outcome was wanted, but the two verbs disagreed on
the lane rule — a wq#2997 might land the wrong way next time, from an agent that never checked
with Frank at all.

Fix: `cmd_done` now checks the ticket's lane before anything else. A 'user'-lane ticket refuses
`done` the same way `claim`/`hold` do — UNLESS the caller passes `--frank-directed "<reason>"`,
which is required precisely because 'user'-lane tickets can never be claimed first, so there is
no claim record to fall back on for "who authorized this". The reason is stamped onto the
ticket's notes as `[FRANK-DIRECTED: ...]`, on the record, the same way `--no-artifact` /
`--unlanded` / `--reclose` stamp their own overrides. All of done's other gates (evidence,
deliverable, landing, ops-deploy) still apply on top of this — the flag only lifts the lane
block, not the rest of `done`'s evidence bar.

Same keeper shape as ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py: `wq` is
rewritten in place by whichever lane touches it next, so the patch needs re-applying, not just
applying once.

    ensure_wq_user_lane_done_gate.py --check [--json]   is the live wq patched?
    ensure_wq_user_lane_done_gate.py --apply            patch it (backup, verify, roll back on failure)
"""
import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time

LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

DONE_PARSER_OLD = r'''s.add_argument("--reclose",default=None,metavar="REASON",help="wq#1116: overwrite the notes of an ALREADY-done ticket, on the record — refused silently otherwise"); s.add_argument("--agent",default=None); s.add_argument("--fence",type=int,default=None,help=FENCE_HELP); s.set_defaults(f=cmd_done)'''

DONE_PARSER_NEW = r'''s.add_argument("--reclose",default=None,metavar="REASON",help="wq#1116: overwrite the notes of an ALREADY-done ticket, on the record — refused silently otherwise"); s.add_argument("--frank-directed",dest="frank_directed",default=None,metavar="REASON",help="wq#2997: required to close a 'user'-lane ticket — claim already refuses that lane unconditionally (Frank-only, never agent-claimable) and done now agrees instead of closing an unclaimed row silently. Record how Frank directed the outcome (e.g. in chat)."); s.add_argument("--agent",default=None); s.add_argument("--fence",type=int,default=None,help=FENCE_HELP); s.set_defaults(f=cmd_done)'''

DONE_HEAD_OLD = '''def cmd_done(a):
    notes = getattr(a, "notes", "") or ""
    no_artifact = getattr(a, "no_artifact", None)
'''

DONE_HEAD_NEW = '''def cmd_done(a):
    notes = getattr(a, "notes", "") or ""
    no_artifact = getattr(a, "no_artifact", None)
    frank_directed = getattr(a, "frank_directed", None)
    # wq#2997: `claim` refuses the 'user' lane unconditionally (Frank-only, never
    # agent-claimable) but `done` had no matching check — a 'user'-lane ticket can never
    # carry a live claim (claim always refuses it), so owner_ok()'s `if not claimed_by:
    # return True` short-circuit left `done` free to close it with no claim on record at all
    # (wq#2997: wq#1304 closed this way). --frank-directed is the on-the-record override,
    # required because there is no claim to point at for "who authorized this".
    _c2997 = conn()
    try:
        _lane_row = _c2997.execute("SELECT lane FROM tasks WHERE id=?", (a.id,)).fetchone()
    finally:
        _c2997.close()
    if _lane_row and _lane_row[0] == 'user' and not frank_directed:
        eprint(f"REFUSED: wq#{a.id} is in the 'user' lane — Frank-only, never agent-claimable "
              f"(wq#2997: `done` now agrees with `claim`'s refusal). If Frank directed this "
              f"outcome (e.g. in chat), record it: wq done {a.id} --frank-directed \\"<how Frank "
              f"directed this>\\" alongside the usual evidence.")
        sys.exit(10)
'''

DONE_TAIL_OLD = '''    unlanded = getattr(a, "unlanded", None)
    if unlanded:
        a.notes = ((getattr(a, "notes", "") or "") +
                   (" " if getattr(a, "notes", "") else "") + f"[UNLANDED: {unlanded}]").strip()
    _finish(a, 'done')
    print(f"done {a.id} (subsystem locks released)")
'''

DONE_TAIL_NEW = '''    unlanded = getattr(a, "unlanded", None)
    if unlanded:
        a.notes = ((getattr(a, "notes", "") or "") +
                   (" " if getattr(a, "notes", "") else "") + f"[UNLANDED: {unlanded}]").strip()
    if frank_directed:
        a.notes = ((getattr(a, "notes", "") or "") +
                   (" " if getattr(a, "notes", "") else "") + f"[FRANK-DIRECTED: {frank_directed}]").strip()
    _finish(a, 'done')
    print(f"done {a.id} (subsystem locks released)")
'''

# (name, old, new, expected occurrences). Each `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched.
PATCHES = [
    ("done-parser-frank-directed-flag", DONE_PARSER_OLD, DONE_PARSER_NEW, 1),
    ("done-head-lane-gate", DONE_HEAD_OLD, DONE_HEAD_NEW, 1),
    ("done-tail-frank-directed-stamp", DONE_TAIL_OLD, DONE_TAIL_NEW, 1),
]


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
    """Prove the candidate on a scratch queue before it goes anywhere near the live binary:
    a 'user'-lane ticket refuses `done` without --frank-directed, succeeds with it (and stamps
    the reason on the record), and an ordinary lane's `done` is completely unaffected."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq2997-verify")

    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_DISPATCHER_CONFIG"] = os.path.join(d, "no-such-dispatcher-config.json")
    env["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2997/router.py"  # wq#2139 lane guard fails open

    def wq(*args, agent=None):
        e = dict(env)
        if agent is not None:
            e["WQ_AGENT"] = agent
        else:
            e.pop("WQ_AGENT", None)
        return subprocess.run([sys.executable, candidate, *args],
                              capture_output=True, text=True, env=e)

    def add(title, lane, *extra):
        p = wq("add", "--lane", lane, "--title", title, *extra, agent="wq2997-verify")
        m = re.search(r"added task (\d+)", p.stdout)
        if p.returncode != 0 or not m:
            raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
        return m.group(1)

    # 1. a 'user'-lane ticket refuses `done` without --frank-directed. --no-probe sidesteps
    #    the unrelated wq#1676 liveness-tag gate on `add` — a Frank-lane ticket needs a
    #    Liveness:/[NO-PROBE:] tag of its own, orthogonal to this patch's `done` gate.
    tid = add("wq2997 verify: user lane, no flag", "user",
             "--no-probe", "wq2997 verify scratch ticket")
    p = wq("done", tid, "--no-artifact", "verify probe")
    if p.returncode == 0:
        raise RuntimeError(f"done on a 'user'-lane ticket without --frank-directed should have "
                           f"refused: {p.stdout}{p.stderr}")
    if "wq#2997" not in p.stdout and "wq#2997" not in p.stderr:
        raise RuntimeError(f"user-lane done refusal is missing its wq#2997 explanation: "
                           f"stdout={p.stdout!r} stderr={p.stderr!r}")

    # 2. the same ticket closes clean once --frank-directed is on the record, and the reason
    #    is stamped into the notes.
    p = wq("done", tid, "--no-artifact", "verify probe",
          "--frank-directed", "Frank directed this in chat (wq2997 verify)")
    if p.returncode != 0 or f"done {tid}" not in p.stdout:
        raise RuntimeError(f"done with --frank-directed should have succeeded: "
                           f"{p.stdout}{p.stderr}")
    p = wq("show", tid)
    if "[FRANK-DIRECTED: Frank directed this in chat" not in p.stdout:
        raise RuntimeError(f"--frank-directed reason was not stamped onto the ticket: {p.stdout}")

    # 3. an ordinary lane's `done` is unaffected — no flag needed, no refusal.
    tid2 = add("wq2997 verify: ordinary lane unaffected", "auto")
    p = wq("claim", "wq2997-verify-holder", "--id", tid2)
    if f"CLAIMED {tid2}" not in p.stdout:
        raise RuntimeError(f"scratch claim (ordinary lane) failed: {p.stdout}{p.stderr}")
    p = wq("done", tid2, "--no-artifact", "verify probe", agent="wq2997-verify-holder")
    if p.returncode != 0 or f"done {tid2}" not in p.stdout:
        raise RuntimeError(f"done on an ordinary lane should be unaffected by this patch: "
                           f"{p.stdout}{p.stderr}")


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

    backup = f"{a.wq}.bak.wq2997.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq2997-cand"), "wq")
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
