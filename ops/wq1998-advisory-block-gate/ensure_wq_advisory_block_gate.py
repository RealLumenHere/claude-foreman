#!/usr/bin/env python3
"""Keep the wq#1676 liveness creation gate ADVISORY at `wq block` (wq#1998).

`cmd_block` calls `_liveness_creation_check(..., verb="block")`. When the ticket carries no
`Liveness:` line that helper eprints `REFUSED: wq#1478/wq#1676 liveness gate — block not done`
and `sys.exit(6)` — which returns BEFORE `cmd_block` reaches `_finish(a, 'blocked')`. The block
never lands and the row stays `claimed` until its lease expires. That is strictly worse than an
untagged block: `liveness_clear.age_alarm()` can still find an untagged blocked row, but nothing
finds a row a worker has already walked away from. A retreat must always land.

wq#1998 fixed it by giving the helper a `fatal` parameter (default True — `add` and `decide` are
unchanged and still refuse) and having `cmd_block` pass `fatal=False`, so an untagged block warns
and files. The advisory path deliberately does NOT stamp `[NO-PROBE: ...]`: that marker is what
`age_alarm()` skips on, so stamping it would silence the 7-day stale-gate alarm for exactly the
population it exists to catch (harness/fworker.py:576-589).

wq#2015 is why this keeper exists. wq#1998 was deployed to the live `/home/lumen/bin/wq` but never
committed to the bin repo, so wq#1987's content-divergence sweep reconciled the file to the repo
and silently reverted it — 17 harness tests went red on one cause. The patch is committed now
(lumen-bin PR #97), and this keeper is the re-apply path for the next time a lane rewrites `wq` in
place, same shape as ops/wq1539-release-dead-worker-order/.

    ensure_wq_advisory_block_gate.py --check [--json]   is the live wq patched?
    ensure_wq_advisory_block_gate.py --apply            patch it (backup, verify, roll back on failure)
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
REPO_HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "harness")

SIG_OLD = '''                              liveness=None, no_probe=None, accept_probe=False):
'''
SIG_NEW = '''                              liveness=None, no_probe=None, accept_probe=False, fatal=True):
'''

DOC_OLD = '''    --force files it anyway, verdict printed. Returns the (possibly Liveness:/[NO-PROBE:
    -augmented) detail text; callers persist it (add: before INSERT; block/decide: UPDATE)."""
'''
DOC_NEW = '''    --force files it anyway, verdict printed. Returns the (possibly Liveness:/[NO-PROBE:
    -augmented) detail text; callers persist it (add: before INSERT; block/decide: UPDATE).
    fatal=False (wq#1998, cmd_block's only caller) downgrades the refusal to a warning:
    sys.exit(6) here returns before cmd_block reaches _finish(a, 'blocked'), so a refused
    block leaves the row claimed until its lease expires, which is strictly worse than an
    untagged block the age alarm can still find. A retreat must always land."""
'''

REFUSE_OLD = '''    eprint(f"REFUSED: wq#1478/wq#1676 liveness gate — {verb} not done. {msg}")
'''
REFUSE_NEW = '''    if not fatal:
        # wq#1998: advisory at `block` — warn and file. Do NOT stamp [NO-PROBE: ...] here:
        # that marker is exactly what liveness_clear.age_alarm() skips on, so stamping it
        # would silence the 7-day stale-gate alarm for the untagged blocks it exists to catch.
        warn(f"liveness {verdict} at {verb}: {msg} (advisory — NOT enforced at block, wq#1998)")
        return detail
    eprint(f"REFUSED: wq#1478/wq#1676 liveness gate — {verb} not done. {msg}")
'''

CALL_OLD = '''            new_detail = _liveness_creation_check(title, detail, lane, True, verb="block",
                                                  liveness=getattr(a, "liveness", None),
                                                  no_probe=getattr(a, "no_probe", None),
                                                  accept_probe=getattr(a, "accept_probe", False))
'''
CALL_NEW = '''            # wq#1998: fatal=False. The gate is advisory HERE and nowhere else — a refused
            # block returns before _finish(a, 'blocked') below and strands the row 'claimed'
            # under a caller that has already given up. A retreat must always land.
            new_detail = _liveness_creation_check(title, detail, lane, True, verb="block",
                                                  liveness=getattr(a, "liveness", None),
                                                  no_probe=getattr(a, "no_probe", None),
                                                  accept_probe=getattr(a, "accept_probe", False),
                                                  fatal=False)
'''

# (name, old, new, expected occurrences). Each `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched.
PATCHES = [
    ("liveness-check-fatal-param", SIG_OLD, SIG_NEW, 1),
    ("liveness-check-docstring", DOC_OLD, DOC_NEW, 1),
    ("liveness-check-advisory-branch", REFUSE_OLD, REFUSE_NEW, 1),
    ("cmd-block-passes-fatal-false", CALL_OLD, CALL_NEW, 1),
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
    """Prove the candidate on a scratch queue: an untagged `block` LANDS (rc 0, row blocked,
    advisory warning on stderr, no [NO-PROBE:] stamp in the detail), while the same missing tag
    at `add --frank-gated` still refuses with rc 6 — the gate is advisory at block and nowhere
    else."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq1998-verify")
    db = os.path.join(d, "q.db")

    env = dict(os.environ)
    env["WQ_DB"] = db
    env["FOREMAN_HARNESS"] = env.get("FOREMAN_HARNESS", REPO_HARNESS)
    env.pop("FOREMAN_LIVENESS_CLEAR", None)

    def wq(*args, agent=None):
        e = dict(env)
        if agent is not None:
            e["WQ_AGENT"] = agent
        else:
            e.pop("WQ_AGENT", None)
        return subprocess.run([sys.executable, candidate, *args],
                              capture_output=True, text=True, env=e)

    def row(tid):
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT status, detail FROM tasks WHERE id=?", (tid,)).fetchone()
        c.close()
        return r

    p = wq("add", "--lane", "build", "--title", "wq1998 verify: untagged block must land",
           agent="wq1998-verify")
    if p.returncode != 0 or not p.stdout.startswith("added"):
        raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
    tid = p.stdout.split()[2]

    holder = f"fworker-wq{tid}"
    p = wq("claim", holder, "--id", tid)
    if f"CLAIMED {tid}" not in p.stdout:
        raise RuntimeError(f"scratch claim failed: {p.stdout}{p.stderr}")

    p = wq("block", tid, "waiting on something untagged", agent=holder)
    if p.returncode != 0:
        raise RuntimeError(f"an untagged block must land, got rc={p.returncode}: {p.stdout}{p.stderr}")
    if "NOT enforced at block" not in p.stderr:
        raise RuntimeError(f"expected the advisory warning on stderr, got: {p.stderr!r}")
    r = row(tid)
    if r["status"] != "blocked":
        raise RuntimeError(f"the row must land 'blocked', got {r['status']!r} — this is the "
                           f"stranded-claim regression wq#1998 exists to prevent")
    if "[NO-PROBE:" in (r["detail"] or ""):
        raise RuntimeError("the advisory path must not stamp [NO-PROBE: ...] — age_alarm() "
                           "skips on that marker")

    # The same missing tag is still FATAL at the add on-ramp (wq#1676 unchanged).
    p = wq("add", "--lane", "user", "--frank-gated", "--title",
           "wq1998 verify: untagged frank-gate still refused", agent="wq1998-verify")
    if p.returncode != 6 or "REFUSED" not in p.stderr:
        raise RuntimeError(f"add --frank-gated with no Liveness: must still refuse with rc 6, "
                           f"got rc={p.returncode}: {p.stdout}{p.stderr}")


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

    backup = f"{a.wq}.bak.wq1998.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq1998-cand"), "wq")
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
