#!/usr/bin/env python3
"""Keep `wq`'s CLOSE-path refusals on stderr (wq#1479).

`/home/lumen/bin/wq` printed every REFUSED on stdout. On 2026-09-07 an orchestrator read
`tail -1` of a refused `wq done` and reported wq#1133 and wq#1296 closed; both were still
`blocked`. The last line of a multi-line refusal on stdout is indistinguishable from an
ordinary trailing note — on stderr it cannot be mistaken for the command's answer.

Scope is deliberately the close path only — `_finish` (done/release/block), `cmd_done`'s
two evidence gates, `owner_ok`, `cmd_heartbeat`, `cmd_hold`. `cmd_claim`'s refusals stay on
stdout: /home/lumen/bin/probe_wq582.py and probe_wq594.py assert them there, and moving
them buys nothing this ticket is about.

Same shape as ops/wq841-rework-durability/ensure_wq_reopened.py, and for the same reason:
`wq` is rewritten in place by whichever lane touches it next, so the patch needs a keeper
rather than a one-time application.

    ensure_wq_refuse_stderr.py --check [--json]     is the live wq patched?
    ensure_wq_refuse_stderr.py --apply              patch it (backup, verify, roll back on failure)
"""
import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import time

LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

EPRINT_DEF = '''def eprint(*a, **k):
    """wq#1479: a refusal is an error, and errors go to stderr. `wq done` used to print
    REFUSED on stdout, so an orchestrator reading `tail -1` of a refused close saw the
    last line of the explanation and reported two still-blocked tickets as done."""
    k["file"] = sys.stderr
    print(*a, **k)
'''

# (name, old, new, expected occurrences). Each `old` is matched verbatim; `--apply` refuses
# unless the count is exactly what is declared here, so a wq that has moved on fails loudly
# instead of being half-patched.
PATCHES = [
    ("eprint-helper",
     'def warn(msg): sys.stderr.write(f"WARNING: {msg}\\n")\n',
     'def warn(msg): sys.stderr.write(f"WARNING: {msg}\\n")\n' + EPRINT_DEF,
     1),
    ("owner_ok",
     '    print(f"REFUSED: wq#{tid} is held by',
     '    eprint(f"REFUSED: wq#{tid} is held by',
     1),
    ("heartbeat-unclaimed",
     'print(f"REFUSED: task {a.id} is not claimed (status=',
     'eprint(f"REFUSED: task {a.id} is not claimed (status=',
     1),
    ("finish-no-such-task",
     'c.execute("COMMIT"); c.close(); print(f"REFUSED: no such task {a.id}"); sys.exit(1)',
     'c.execute("COMMIT"); c.close(); eprint(f"REFUSED: no such task {a.id}"); sys.exit(1)',
     2),
    ("finish-already-done",
     'print(f"REFUSED: wq#{a.id} is already done (closed',
     'eprint(f"REFUSED: wq#{a.id} is already done (closed',
     1),
    ("finish-stale-fence",
     'print(f"REFUSED: {new_status} on wq#{a.id} matched no row under fence',
     'eprint(f"REFUSED: {new_status} on wq#{a.id} matched no row under fence',
     1),
    ("done-evidence-gate",
     'print(f"REFUSED: wq#759 evidence gate',
     'eprint(f"REFUSED: wq#759 evidence gate',
     1),
    ("done-merge-state-gate",
     'print(f"REFUSED: wq#899 merge-state gate',
     'eprint(f"REFUSED: wq#899 merge-state gate',
     1),
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
    a refused close puts REFUSED on stderr and NOTHING on stdout, and an ordinary close
    still works."""
    py_compile.compile(candidate, doraise=True)
    d = tempfile.mkdtemp(prefix="wq1479-verify")
    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_AGENT"] = "wq1479-verify"

    def wq(*args):
        return subprocess.run([sys.executable, candidate, *args],
                              capture_output=True, text=True, env=env)

    p = wq("add", "--lane", "auto", "--title", "wq1479 verify ticket")
    if p.returncode != 0 or not p.stdout.startswith("added"):
        raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
    tid = p.stdout.split()[2]
    wq("claim", "--id", tid)

    p = wq("done", tid, "--notes", "no artifact anywhere in this string")
    if p.returncode == 0:
        raise RuntimeError("wq#759 evidence gate did not fire — cannot prove the patch")
    if "REFUSED" not in p.stderr:
        raise RuntimeError(f"refusal is not on stderr: stdout={p.stdout!r} stderr={p.stderr!r}")
    if "REFUSED" in p.stdout:
        raise RuntimeError(f"refusal is STILL on stdout: {p.stdout!r}")

    p = wq("done", tid, "--notes", "ok", "--no-artifact", "verify")
    if p.returncode != 0:
        raise RuntimeError(f"ordinary close regressed: {p.stdout}{p.stderr}")
    c = sqlite3.connect(env["WQ_DB"])
    st = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.close()
    if st != "done":
        raise RuntimeError(f"ordinary close left status={st!r}")


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
                        [f"  {k:24} {v}" for k, v in st.items()]))
        if not a.apply:
            return 0 if ok else 1

    if ok:
        print("already applied — nothing to do")
        return 0

    backup = f"{a.wq}.bak.wq1479.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(a.wq, backup)
    cand = os.path.join(tempfile.mkdtemp(prefix="wq1479-cand"), "wq")
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
