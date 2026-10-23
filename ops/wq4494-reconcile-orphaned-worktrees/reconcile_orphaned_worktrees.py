#!/usr/bin/env python3
"""wq#4494 — reconcile orphaned unregistered checkouts under a nested worktrees
copy (found at /home/lumen/worktrees/worktrees: 2059 wqNNNN-aN dirs plus
repo-verify-main, 46G, dated 2026-08-22..2026-10-02).

lumen-prune-worktrees.timer (ops/wq2620-prune-stale-worktrees/prune_worktrees.py)
only scans DIRECT children of its roots and only acts on a directory that
`git worktree list --porcelain` can vouch for. Every dir under this nested
copy fails that check for the same reason: its `.git` file still points at an
admin dir (REPO/.git/worktrees/NAME) that no longer exists, so git can't
report dirty/unpushed state and the safety checks in wq2620 can't clear it —
by design, since an unreadable answer must never read as a safe one.

This tool is the next step for exactly that debris: it does NOT trust the
directory's git state (there is none to trust). Instead, for each entry it:
  1. Confirms orphan status itself — the `.git` pointer resolves to a gitdir
     that genuinely does not exist on disk (see `is_orphaned`). Anything that
     IS still registered is left alone; that is wq2620's job, not this one.
  2. Maps the directory name to a wq ticket (`wqNNNN-aN`) and requires the
     ticket to be `status='done'` in the live queue db before touching it.
     No ticket id in the name, or no row, or a non-done status, all skip —
     fail closed, matching wq2620's own `wq_claim_is_live` convention.
  3. For an eligible dir, archives it (tar+zstd) to `--archive-root`,
     test-extracts the archive into a scratch dir and byte-compares it
     against the source tree, and only deletes the source once that
     round-trip verifies. A failed archive or a failed verify skips instead
     of deleting — nothing is destroyed on a hunch (ISSUE-LOG #17: done is a
     replay, not a claim).
  4. Appends one JSON line per run to `--log` (default below) with every
     path's action and reason, dry-run or applied — the same paper-trail
     convention wq2620 uses.

    reconcile_orphaned_worktrees.py                  # dry run, human summary
    reconcile_orphaned_worktrees.py --json           # dry run, full JSON
    reconcile_orphaned_worktrees.py --apply          # archive + delete for real
    reconcile_orphaned_worktrees.py --root R         # override the scanned dir
"""
import argparse
import datetime
import filecmp
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

DEFAULT_ROOT = "/home/lumen/worktrees/worktrees"
DEFAULT_ARCHIVE_ROOT = "/home/lumen/backups/wq4494-orphaned-worktrees"
WQ_DB = os.environ.get("PRUNE_WQ_DB", "/home/lumen/agents/work-queue/queue.db")
LOG_PATH = os.environ.get(
    "RECONCILE_LOG", "/home/lumen/kb/logs/wq4494-reconcile-orphaned-worktrees.jsonl")

# fworker's per-ticket shape (harness/fworker.py, bin/wq FWORKER_IDENTITY_RE) —
# same pattern ops/wq2620-prune-stale-worktrees/prune_worktrees.py matches on.
FWORKER_WT_RE = re.compile(r"^wq(\d+)-a\d+$")

_LOG_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def sh(args, timeout=120):
    """(returncode, stdout, stderr) for `args`, or (1, "", err) on any failure."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


# ── orphan detection ─────────────────────────────────────────────────────────

def gitdir_of(path):
    """gitdir string a `path/.git` FILE points at, or None if `path` has no
    `.git` file, it isn't a `gitdir:` pointer, or it can't be read — i.e. not a
    linked-worktree pointer at all (a stray dir, or a primary checkout whose
    `.git` is a directory)."""
    gf = os.path.join(path, ".git")
    if not os.path.isfile(gf):
        return None
    try:
        with open(gf) as f:
            line = f.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    return line.split("gitdir:", 1)[1].strip()


def repo_of_gitdir(gitdir):
    """Repo root for an admin `gitdir` of the form REPO/.git/worktrees/NAME, or
    None if it isn't shaped that way."""
    marker = "/.git/worktrees/"
    if marker not in gitdir:
        return None
    return gitdir.split(marker, 1)[0]


def is_orphaned(path):
    """(True, reason) iff `path` is a linked-worktree pointer whose admin
    gitdir no longer exists on disk — the defining property of this debris:
    git itself cannot vouch for these (wq#4494). (False, reason) for anything
    still registered, or anything that was never a linked worktree at all —
    both out of this tool's scope, left for wq2620 or for manual review."""
    gitdir = gitdir_of(path)
    if gitdir is None:
        return False, "not a linked-worktree `.git` pointer"
    if os.path.isdir(gitdir):
        return False, f"admin gitdir {gitdir} still present — registered, out of scope"
    return True, f"admin gitdir {gitdir} missing"


# ── ticket lookup ────────────────────────────────────────────────────────────

def _db_conn():
    if not os.path.exists(WQ_DB):
        return None
    return sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True, timeout=10)


def ticket_status(ticket_id):
    """`tasks.status` for `ticket_id`, or None if there is no row or the db
    can't be read — callers fail closed on None, same as wq2620."""
    conn = _db_conn()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (ticket_id,)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    return row[0] if row else None


# ── classification ───────────────────────────────────────────────────────────

def classify(path):
    """(action, reason) — action is 'archive' or 'skip'. Every skip path is a
    distinct, logged reason; nothing falls through silently."""
    orphaned, reason = is_orphaned(path)
    if not orphaned:
        return "skip", reason
    name = os.path.basename(path)
    m = FWORKER_WT_RE.match(name)
    if not m:
        return "skip", "no wq ticket id in name — needs manual review"
    ticket_id = int(m.group(1))
    status = ticket_status(ticket_id)
    if status is None:
        return "skip", f"wq#{ticket_id} not found in db — fail closed"
    if status != "done":
        return "skip", f"wq#{ticket_id} status={status!r}, not done — fail closed"
    return "archive", f"wq#{ticket_id} done, admin gitdir gone"


# ── archive + verify ─────────────────────────────────────────────────────────

def archive_dir(path, archive_root):
    """tar+zstd `path` into `archive_root/<name>-<stamp>.tar.zst`, test-extract
    it into a scratch dir, and byte-compare every file against the source.
    Returns (archive_path, None) only once that round-trip verifies;
    (None, error) otherwise — the source is never deleted on an unverified
    archive."""
    name = os.path.basename(path)
    parent = os.path.dirname(path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        os.makedirs(archive_root, exist_ok=True)
    except OSError as e:
        return None, f"mkdir {archive_root} failed: {e}"
    archive_path = os.path.join(archive_root, f"{name}-{stamp}.tar.zst")

    tar = subprocess.Popen(["tar", "-cf", "-", "-C", parent, name],
                            stdout=subprocess.PIPE)
    with open(archive_path, "wb") as out:
        zstd = subprocess.run(["zstd", "-q", "-c"], stdin=tar.stdout, stdout=out)
    tar.stdout.close()
    tar_rc = tar.wait()
    if tar_rc != 0 or zstd.returncode != 0:
        try:
            os.remove(archive_path)
        except OSError:
            pass
        return None, f"tar/zstd failed (tar rc={tar_rc}, zstd rc={zstd.returncode})"

    with tempfile.TemporaryDirectory(prefix="wq4494-verify-") as scratch:
        unzstd = subprocess.Popen(["zstd", "-q", "-d", "-c", archive_path],
                                   stdout=subprocess.PIPE)
        untar = subprocess.run(["tar", "-xf", "-", "-C", scratch],
                                stdin=unzstd.stdout)
        unzstd.stdout.close()
        unzstd_rc = unzstd.wait()
        if unzstd_rc != 0 or untar.returncode != 0:
            return None, f"test-extract failed (zstd rc={unzstd_rc}, tar rc={untar.returncode})"

        extracted = os.path.join(scratch, name)
        mismatch = _tree_diff(path, extracted)
        if mismatch:
            return None, f"verify mismatch: {mismatch}"

    return archive_path, None


def _tree_diff(a, b):
    """First mismatching relative path between trees `a` and `b`, or None if
    they are identical (contents, not just names — `filecmp.cmpfiles` with
    `shallow=False` reads every file)."""
    cmp = filecmp.dircmp(a, b)
    return _dircmp_mismatch(cmp, "")


def _dircmp_mismatch(cmp, prefix):
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return f"{prefix or '.'}: left_only={cmp.left_only} right_only={cmp.right_only}"
    _, mismatch, errors = filecmp.cmpfiles(cmp.left, cmp.right, cmp.common_files,
                                            shallow=False)
    if mismatch or errors:
        return f"{prefix or '.'}: mismatch={mismatch} errors={errors}"
    for sub, subcmp in cmp.subdirs.items():
        found = _dircmp_mismatch(subcmp, os.path.join(prefix, sub))
        if found:
            return found
    return None


# ── size / logging ───────────────────────────────────────────────────────────

def dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, fname)).st_size
            except OSError:
                pass
    return total


def human(n):
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}P"


def append_log(path, record):
    record = dict(record, ts=time.strftime(_LOG_TS_FMT, time.gmtime()))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ── run ──────────────────────────────────────────────────────────────────────

def run(root, apply, archive_root):
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return {"error": f"cannot list {root}: {e}"}, []

    results = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        action, reason = classify(path)
        rec = {"path": path, "action": action, "reason": reason}
        if action == "archive":
            rec["bytes"] = dir_size(path)
            if apply:
                archive_path, err = archive_dir(path, archive_root)
                if archive_path is not None:
                    shutil.rmtree(path)
                    rec["archived_to"] = archive_path
                    rec["removed"] = True
                else:
                    rec["action"] = "skip"
                    rec["removed"] = False
                    rec["reason"] = f"archive failed: {err}"
        results.append(rec)
    return None, results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--apply", action="store_true",
                     help="actually archive + delete; default is dry-run")
    ap.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args(argv)

    error, results = run(args.root, args.apply, args.archive_root)
    if error:
        print(json.dumps(error) if args.json else error["error"], file=sys.stderr)
        return 1

    archived = [r for r in results if r["action"] == "archive"]
    size_total = sum(r.get("bytes", 0) for r in archived)
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "root": args.root,
        "scanned": len(results),
        "archivable": len(archived),
        "skipped": len(results) - len(archived),
        "bytes": size_total,
    }
    append_log(args.log, {"summary": summary, "results": results})

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        print(f"{summary['mode']}: scanned={summary['scanned']} "
              f"archivable={summary['archivable']} skipped={summary['skipped']} "
              f"size={human(size_total)}")
        for r in archived:
            tag = "REMOVED" if r.get("removed") else "ARCHIVE"
            print(f"  {tag} {r['path']} ({human(r['bytes'])}) — {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
