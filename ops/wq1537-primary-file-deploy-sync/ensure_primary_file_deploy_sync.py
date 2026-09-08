#!/usr/bin/env python3
"""Sync one tracked file in a PRIMARY tree to origin/<default>, independent of whatever
branch HEAD the primary happens to be parked on (wq#1537).

The failure this exists for: wq#1536 merged `lumen-pr ffprimary` to origin/main, but
/home/lumen/bin's HEAD was parked on a feature branch cut before that PR (another lane's
WIP, still uncommitted 2026-09-08) — so the DEPLOYED `lumen-pr` kept running the pre-#1536
code and silently leaving primaries behind on every `land`. The sanctioned resync tool,
`sync-live` (wq#1256), fast-forwards the whole tree's HEAD and refuses outright on ANY
dirty tracked file — correct for its job (a wrong ref move is a worse failure than a stale
tree), but that means one unrelated in-flight ticket's uncommitted edits anywhere in the
repo block every OTHER file's deploy too, indefinitely (wq#1538 is that exact backlog: 9
tickets of live-only hunks blocking `wq`, discovered the same session as this one).

`lumen-pr` itself carries no local edits in that tree — it is simply stale, not dirty. The
class fix `wq#1537` asked for is a deploy step that reaches a single already-reviewed file
(content that already landed on origin/<default>, so nothing unreviewed lands) without
touching HEAD or any other path, the same "ensure script" shape used for wq#1479/wq#1498
(ops/wq1479-close-verdict, ops/wq1498-dead-worker-release) — except those hand-patch known
anchors while this one just takes the file whole from the remote, since the source of truth
already exists there. Refuses if the LIVE file itself has an uncommitted diff (that WIP is
this tool's one and only guard: it must never overwrite an edit that exists nowhere else).

    ensure_primary_file_deploy_sync.py --check [--json]   is <path> at origin/<branch>?
    ensure_primary_file_deploy_sync.py --apply             sync it (backup, verify, roll back)

Env overrides (all optional): WQ1537_REPO (default /home/lumen/bin), WQ1537_PATH (default
lumen-pr), WQ1537_BRANCH (default main), WQ1537_REMOTE (default origin) — so a test or a
future occurrence of this same class can point this at a scratch repo or a different file
without editing the script.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_REPO = os.environ.get("WQ1537_REPO", "/home/lumen/bin")
DEFAULT_PATH = os.environ.get("WQ1537_PATH", "lumen-pr")
DEFAULT_BRANCH = os.environ.get("WQ1537_BRANCH", "main")
DEFAULT_REMOTE = os.environ.get("WQ1537_REMOTE", "origin")


class SyncError(RuntimeError):
    pass


def git(repo, *args, check=True, timeout=60):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       timeout=timeout)
    if check and p.returncode != 0:
        raise SyncError(f"git -C {repo} {' '.join(args)} failed rc={p.returncode}: "
                        f"{p.stderr.strip()}")
    return p


def remote_ref(remote, branch):
    return f"{remote}/{branch}"


def blob_and_exec(repo, ref, path):
    """(content bytes, is_executable) of path at ref. Raises if the path doesn't exist there
    — a caller pointing this at a path the remote never had is a config mistake, not a state
    this tool should silently paper over."""
    ls = git(repo, "ls-tree", ref, "--", path).stdout.strip()
    if not ls:
        raise SyncError(f"{ref}:{path} does not exist")
    mode = ls.split()[0]
    p = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        raise SyncError(f"git show {ref}:{path} failed: {p.stderr.decode(errors='replace')}")
    return p.stdout, mode == "100755"


def status(repo, path, remote, branch, fetch=True):
    if fetch:
        git(repo, "fetch", remote, branch, check=False)
    ref = remote_ref(remote, branch)
    live_path = os.path.join(repo, path)
    live_bytes = open(live_path, "rb").read() if os.path.exists(live_path) else None
    target_bytes, target_exec = blob_and_exec(repo, ref, path)
    # Dirty means THIS path has an uncommitted edit against whatever HEAD currently is —
    # the one case this tool must never overwrite, since that content exists nowhere else.
    # Untracked/other-path dirt (wq#1538's territory) is irrelevant to a single-file sync.
    dirty = bool(git(repo, "status", "--porcelain", "--", path).stdout.strip())
    return {
        "repo": repo, "path": path, "remote_ref": ref,
        "in_sync": live_bytes == target_bytes,
        "dirty": dirty,
        "target_sha": git(repo, "rev-parse", ref).stdout.strip(),
        "_target_bytes": target_bytes,
        "_target_exec": target_exec,
    }


def syntax_check(path):
    """Best-effort: a candidate that fails to even parse must never reach the live path.
    Dispatches on shebang; a file this tool doesn't recognize is passed through unchecked
    rather than refused, since "unknown shebang" is not evidence of a broken file."""
    with open(path, "rb") as f:
        first_line = f.readline()
    if b"bash" in first_line:
        p = subprocess.run(["bash", "-n", path], capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise SyncError(f"bash -n {path} failed: {p.stderr.strip()}")
    elif b"python" in first_line:
        p = subprocess.run([sys.executable, "-m", "py_compile", path],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise SyncError(f"py_compile {path} failed: {p.stderr.strip()}")


def apply(repo, path, remote, branch):
    st = status(repo, path, remote, branch)
    if st["in_sync"]:
        return st, False
    if st["dirty"]:
        raise SyncError(f"{os.path.join(repo, path)} has an uncommitted local edit — "
                        f"refusing to overwrite content that exists nowhere else. Resolve "
                        f"it (commit, stash, or hand-reconcile) before re-running --apply.")
    live_path = os.path.join(repo, path)
    target_bytes, target_exec = st["_target_bytes"], st["_target_exec"]
    cand_dir = tempfile.mkdtemp(prefix="wq1537-cand")
    cand = os.path.join(cand_dir, os.path.basename(path))
    with open(cand, "wb") as f:
        f.write(target_bytes)
    os.chmod(cand, 0o755 if target_exec else 0o644)
    syntax_check(cand)

    backup = None
    if os.path.exists(live_path):
        backup = f"{live_path}.bak.wq1537.{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(live_path, backup)
    shutil.copyfile(cand, live_path)
    shutil.copymode(cand, live_path)
    try:
        syntax_check(live_path)
        with open(live_path, "rb") as f:
            if f.read() != target_bytes:
                raise SyncError("post-write content mismatch")
    except Exception as e:
        if backup:
            shutil.copyfile(backup, live_path)
            shutil.copymode(backup, live_path)
        raise SyncError(f"applied then ROLLED BACK: {e}") from e
    return status(repo, path, remote, branch, fetch=False), True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    a = ap.parse_args(argv)
    if not (a.check or a.apply):
        ap.error("one of --check / --apply")

    try:
        if a.apply:
            st, changed = apply(a.repo, a.path, a.remote, a.branch)
        else:
            st = status(a.repo, a.path, a.remote, a.branch)
            changed = False
    except SyncError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    report = {"repo": st["repo"], "path": st["path"], "remote_ref": st["remote_ref"],
              "target_sha": st["target_sha"], "in_sync": st["in_sync"], "dirty": st["dirty"],
              "changed": changed}
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        verb = "SYNCED" if changed else ("IN SYNC" if st["in_sync"] else "NOT IN SYNC")
        print(f"{st['repo']}/{st['path']}: {verb} (vs {st['remote_ref']} {st['target_sha'][:7]}"
              f"{', dirty' if st['dirty'] else ''})")
    return 0 if (st["in_sync"] or changed) else 1


if __name__ == "__main__":
    sys.exit(main())
