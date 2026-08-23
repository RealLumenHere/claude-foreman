#!/usr/bin/env python3
"""wq#841 (a2): keep the reopened_count WRITER alive in /home/lumen/bin/wq.

wq#841 replaced a mutable `notes LIKE '%REOPENED%'` marker with tasks.reopened_count, a
monotonic column bumped by wq itself. That closed the reader-side hole. It left a writer-side
one: `wq` is an untracked file that several lanes edit in place, usually by restoring an older
copy and re-adding their own feature on top. On 2026-08-23 12:17Z exactly that happened — the
live binary went back to a pre-wq841 copy, every reopened_count reference vanished, and the
column froze. Nothing complained: the column still existed, the exporter still read it
structurally, so `lumen_queue_reopened_signal_is_structural` kept reporting 1 while the count
could no longer move. A metric that decays because its writer is gone looks exactly like real
improvement — the wq#841 failure mode again, one layer up.

So this script owns the patch, not `patch(1)`:

  --check   is the writer present in the target file? (exit 0 yes / 1 no; --json for machines)
  --apply   put it back, idempotently, and prove it still works before keeping the result

`--apply` is anchor-based rather than a context diff, so it survives unrelated edits to wq —
which is the whole point, since unrelated edits are what keep removing it. It backs the target
up, verifies with py_compile plus a live add/done/reopen round-trip against a scratch DB, and
restores the backup if either fails. Re-running it on a patched file changes nothing.

reports/opsaudit_a13_flow_metrics.py imports MARKERS from here to export
`lumen_queue_reopened_writer_present`, so the writer going missing is now a visible event.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

WQ_BIN = "/home/lumen/bin/wq"

# What "the writer is present" means, structurally. Each entry is (name, regex). The exporter
# imports this list, so the gauge and the applier can never disagree about what patched means.
MARKERS = [
    ("migration", r'ADD COLUMN reopened_count INTEGER DEFAULT 0'),
    ("is_reopen", r'def is_reopen\(old_status, new_status\)'),
    ("bump", r'def bump_reopened\(c, tid\)'),
    ("finish_call", r'bump_reopened\(c, a\.id\) if is_reopen\(r\[2\], new_status\)'),
    ("edit_call", r'bump_reopened\(c, a\.id\) if is_reopen\(prev_status, a\.status\)'),
]

# The 12:17Z revert also dropped wq#741's block_reason/blocked_at migration, while leaving the
# _finish code that WRITES those columns in place. The production DB already has them so nothing
# visibly broke, but every fresh DB — i.e. every test of wq — now dies on `wq done` with "no such
# column: block_reason". That is load-bearing here: _finish is the function this patch hooks, and
# a _finish that raises cannot bump anything. Restored only when absent, and deliberately NOT in
# MARKERS: the gauge below is about the reopened writer, not about wq#741.
PREREQ = (
    '    if "archetype"    not in cols: c.execute("ALTER TABLE tasks ADD COLUMN archetype TEXT")\n',
    '    if "archetype"    not in cols: c.execute("ALTER TABLE tasks ADD COLUMN archetype TEXT")\n'
    '    # wq#741 via wq#841(a2): _finish writes these on every done/block. Lost in the 2026-08-23\n'
    '    # 12:17Z revert of /home/lumen/bin/wq; restored here because a fresh DB is unusable without\n'
    '    # them. block_reason/blocked_at are cleared on release/done so a re-block starts fresh.\n'
    '    if "block_reason" not in cols: c.execute("ALTER TABLE tasks ADD COLUMN block_reason TEXT")\n'
    '    if "blocked_at"   not in cols: c.execute("ALTER TABLE tasks ADD COLUMN blocked_at TEXT")\n',
)
PREREQ_MARKER = r'ADD COLUMN block_reason'

# (find, replace) applied in order, each required to match EXACTLY once. Anchors are lines that
# have survived every rewrite of wq so far: the archetype migration, the _finish/cmd_edit
# signatures, and the one-line cmd_release/cmd_block bodies.
HUNKS = [
    (
        '    if "archetype"    not in cols: c.execute("ALTER TABLE tasks ADD COLUMN archetype TEXT")\n',
        '    if "archetype"    not in cols: c.execute("ALTER TABLE tasks ADD COLUMN archetype TEXT")\n'
        '    # wq#841 (escalated from wq#787\'s verify-pass of wq#763): rework needs a DURABLE signal.\n'
        '    # The old one was `notes LIKE \'%REOPENED%\'`, and `wq done` REPLACES notes wholesale — so a\n'
        '    # ticket reopened, marked, then re-closed silently left the rework population even though\n'
        '    # the rework really happened. reopened_count is monotonic and lives in its own column, so\n'
        '    # nothing any later `wq done`/`wq edit` writes can erase it. ADDITIVE — 0/NULL on every\n'
        '    # pre-existing row. Removing this block breaks the metric SILENTLY; keep it, or\n'
        '    # ops/wq841-rework-durability/ensure_wq_reopened.py --apply puts it back.\n'
        '    if "reopened_count" not in cols: c.execute("ALTER TABLE tasks ADD COLUMN reopened_count INTEGER DEFAULT 0")\n'
        '    if "reopened_at"    not in cols: c.execute("ALTER TABLE tasks ADD COLUMN reopened_at TEXT")\n',
    ),
    (
        'def _finish(a, new_status):\n'
        '    c = conn(); c.isolation_level = None\n',
        '# wq#841: the single definition of "this ticket came back from the dead". Rework is a move\n'
        '# INTO an in-flight state FROM a terminal one. A lease reap or a release of a claim nobody\n'
        '# finished is claimed->open — not rework, and it must not inflate the metric.\n'
        'TERMINAL_STATUSES = ("done", "rejected")\n'
        'INFLIGHT_STATUSES = ("open", "claimed", "blocked")\n'
        'def is_reopen(old_status, new_status):\n'
        '    return (old_status or "") in TERMINAL_STATUSES and (new_status or "") in INFLIGHT_STATUSES\n'
        'def bump_reopened(c, tid):\n'
        '    """Monotonic +1 in its own column. COALESCE covers rows that predate the migration."""\n'
        '    c.execute("UPDATE tasks SET reopened_count=COALESCE(reopened_count,0)+1, reopened_at=? WHERE id=?",\n'
        '              (now(), tid))\n'
        '    return c.execute("SELECT reopened_count FROM tasks WHERE id=?", (tid,)).fetchone()[0]\n'
        '\n'
        'def _finish(a, new_status):\n'
        '    """Returns the new reopened_count if this call was a reopen, else 0 — callers print it, so\n'
        '    a ticket coming back from done/rejected is never a silent event (wq#841)."""\n'
        '    c = conn(); c.isolation_level = None\n',
    ),
    (
        '    r = c.execute("SELECT claimed_by, subsystems FROM tasks WHERE id=?", (a.id,)).fetchone()\n',
        '    r = c.execute("SELECT claimed_by, subsystems, status FROM tasks WHERE id=?", (a.id,)).fetchone()\n',
    ),
    (
        '    c.execute("COMMIT"); c.close()\n'
        'def cmd_done(a):\n',
        '    reopened = bump_reopened(c, a.id) if is_reopen(r[2], new_status) else 0\n'
        '    c.execute("COMMIT"); c.close()\n'
        '    return reopened\n'
        'def cmd_done(a):\n',
    ),
    (
        "def cmd_release(a): _finish(a, 'open');    print(f\"released {a.id} (subsystem locks released)\")\n"
        "def cmd_block(a):   _finish(a, 'blocked'); print(f\"blocked {a.id}: {a.reason} (subsystem locks released)\")\n",
        'def _reopen_tag(n): return f" [REOPENED — reopened_count={n}]" if n else ""\n'
        "def cmd_release(a): print(f\"released {a.id} (subsystem locks released){_reopen_tag(_finish(a, 'open'))}\")\n"
        "def cmd_block(a):   print(f\"blocked {a.id}: {a.reason} (subsystem locks released){_reopen_tag(_finish(a, 'blocked'))}\")\n",
    ),
    (
        'def cmd_edit(a):\n'
        '    c = conn(); fields=[]; vals=[]\n',
        'def cmd_edit(a):\n'
        '    c = conn(); fields=[]; vals=[]\n'
        '    # wq#841: `wq edit --status open` on a done/rejected ticket IS the reopen path the fleet\n'
        '    # actually uses (there is no `wq reopen`). Read the old status before the UPDATE overwrites it.\n'
        '    prev_status = None\n'
        '    if a.status is not None:\n'
        '        row = c.execute("SELECT status FROM tasks WHERE id=?", (a.id,)).fetchone()\n'
        '        prev_status = row[0] if row else None\n',
    ),
    (
        '    vals.append(a.id); c.execute(f"UPDATE tasks SET {\', \'.join(fields)} WHERE id=?", vals); c.commit()\n'
        '    print(f"edited {a.id}: {\', \'.join(x.split(\'=\')[0] for x in fields)}"); c.close()\n',
        '    vals.append(a.id); c.execute(f"UPDATE tasks SET {\', \'.join(fields)} WHERE id=?", vals)\n'
        '    reopened = bump_reopened(c, a.id) if is_reopen(prev_status, a.status) else 0\n'
        '    c.commit()\n'
        '    print(f"edited {a.id}: {\', \'.join(x.split(\'=\')[0] for x in fields)}{_reopen_tag(reopened)}"); c.close()\n',
    ),
]


def check(path=WQ_BIN):
    """Which markers are present in `path`. Never raises — the exporter calls this."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"path": path, "readable": False, "present": False, "missing": [n for n, _ in MARKERS],
                "error": str(exc)}
    missing = [name for name, pat in MARKERS if not re.search(pat, text)]
    return {"path": path, "readable": True, "present": not missing, "missing": missing,
            "partial": bool(missing) and len(missing) < len(MARKERS),
            "prereq_missing": not re.search(PREREQ_MARKER, text)}


def apply_hunks(text, hunks=None):
    """Returns patched text. Raises ValueError if any anchor is missing or ambiguous — a hunk
    that matched twice means wq changed shape, and guessing which copy to patch is how you end
    up with a half-patched binary that imports but miscounts."""
    if hunks is None:
        hunks = list(HUNKS)
        if not re.search(PREREQ_MARKER, text):
            hunks.insert(0, PREREQ)
    for i, (find, repl) in enumerate(hunks, 1):
        n = text.count(find)
        if n != 1:
            raise ValueError(f"hunk {i}: anchor matched {n} times, expected 1 — "
                             f"wq has changed shape, re-derive the hunk. Anchor starts: "
                             f"{find.splitlines()[0][:80]!r}")
        text = text.replace(find, repl)
    return text


def smoke_test(wq_path):
    """Drive the real binary: add -> done -> reopen, and read the column back. py_compile only
    proves it parses; this proves it still counts."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "smoke.db")
        env = dict(os.environ, WQ_DB=db, WQ_AGENT="wq841-ensure-smoke")

        def run(*args):
            return subprocess.run([sys.executable, wq_path, *args], capture_output=True,
                                  text=True, env=env, timeout=60)

        p = run("add", "--lane", "auto", "--title", "wq841 ensure smoke")
        if p.returncode != 0:
            return False, f"add failed: {p.stdout}{p.stderr}"
        tid = p.stdout.strip().split()[2]
        p = run("done", tid, "--notes", "smoke", "--no-artifact", "smoke test")
        if p.returncode != 0:
            return False, f"done failed: {p.stdout}{p.stderr}"
        p = run("edit", tid, "--status", "open")
        if p.returncode != 0:
            return False, f"edit failed: {p.stdout}{p.stderr}"
        c = sqlite3.connect(db)
        (count,) = c.execute("SELECT reopened_count FROM tasks WHERE id=?", (tid,)).fetchone()
        c.close()
        if count != 1:
            return False, f"reopen did not bump the column: reopened_count={count!r}"
        return True, f"add/done/reopen round-trip on a scratch DB left reopened_count={count}"


def apply(path=WQ_BIN, backup=True):
    state = check(path)
    if not state["readable"]:
        return 1, f"cannot read {path}: {state.get('error')}"
    if state["present"] and not state["prereq_missing"]:
        return 0, f"already present in {path} — nothing to do"
    if state["partial"]:
        return 1, (f"{path} is HALF-patched (missing {', '.join(state['missing'])}) — refusing to "
                   f"guess. Restore a known copy, then re-run --apply.")

    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    # A wq can be missing the writer, wq#741's migration, or both — the 12:17Z revert took both,
    # and a later hand-repair put only the writer back. Patch whichever is actually absent.
    hunks = ([PREREQ] if state["prereq_missing"] else []) + ([] if state["present"] else list(HUNKS))
    try:
        patched = apply_hunks(original, hunks)
    except ValueError as exc:
        return 1, str(exc)

    bak = None
    if backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        bak = f"{path}.bak.{stamp}_pre_wq841_ensure"
        shutil.copy2(path, bak)

    mode = os.stat(path).st_mode
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(patched)
    os.chmod(path, mode)

    def rollback(why):
        if bak:
            shutil.copy2(bak, path)
            return 1, f"{why} — rolled back from {bak}"
        return 1, f"{why} — NO BACKUP TAKEN, {path} is left patched and broken"

    p = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
    if p.returncode != 0:
        return rollback(f"py_compile failed: {p.stderr.strip()}")
    ok, detail = smoke_test(path)
    if not ok:
        # Only roll back damage this patch caused. wq arrives here in whatever state the last
        # lane to rewrite it left behind, so a round-trip that was already failing before the
        # hunks went in is not evidence against them — reverting would just discard the fix and
        # leave the same breakage. Reproduce the failure on the untouched text to tell which.
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = os.path.join(tmp, "wq_baseline")
            with open(baseline_path, "w", encoding="utf-8") as fh:
                fh.write(original)
            baseline_ok, baseline_detail = smoke_test(baseline_path)
        if baseline_ok:
            return rollback(f"smoke test failed: {detail}")
        return 0, (f"patched {path} ({'backup ' + bak if bak else 'no backup'}), but the smoke "
                   f"test still fails — PRE-EXISTING, not this patch: unpatched {path} fails the "
                   f"same round-trip ({baseline_detail}). Patched failure: {detail}")
    return 0, f"patched {path} ({'backup ' + bak if bak else 'no backup'}); {detail}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=WQ_BIN, help=f"file to check/patch (default {WQ_BIN})")
    ap.add_argument("--check", action="store_true", help="report whether the writer is present")
    ap.add_argument("--apply", action="store_true", help="put the writer back, idempotently")
    ap.add_argument("--json", action="store_true", help="machine-readable --check output")
    ap.add_argument("--no-backup", action="store_true", help="skip the .bak copy (tests)")
    a = ap.parse_args()
    if a.apply:
        code, msg = apply(a.path, backup=not a.no_backup)
        print(msg)
        return code
    state = check(a.path)
    if a.json:
        print(json.dumps(state, indent=2))
    elif state["present"]:
        print(f"reopened_count writer PRESENT in {state['path']}")
    else:
        print(f"reopened_count writer MISSING from {state['path']} "
              f"(absent: {', '.join(state['missing'])}) — rework_rate is frozen, not improving. "
              f"Re-apply: {os.path.abspath(__file__)} --apply")
    return 0 if state["present"] else 1


if __name__ == "__main__":
    sys.exit(main())
