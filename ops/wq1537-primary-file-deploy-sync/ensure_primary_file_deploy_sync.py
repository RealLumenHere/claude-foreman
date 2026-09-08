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
already exists there. Refuses if the LIVE file itself carries a FOREIGN uncommitted diff
(that WIP is this tool's one and only guard: it must never overwrite an edit that exists
nowhere else).

"Foreign" is the wq#1544 fix. Since --apply deliberately never moves HEAD, the file it just
wrote is permanently "modified" against HEAD, so a plain `git status --porcelain -- path`
made this a one-shot tool: the next time origin/<branch> advanced, --apply refused to
overwrite *its own prior output* (wq#1541 reproduced it; live /home/lumen/bin was already
trapped that way). An uncommitted diff is only a genuine third-party edit when the live
content is neither
  (a) content this tool wrote itself — recorded by blob sha in a sidecar state file under
      the repo's git dir (never in the worktree, so it dirties nothing), nor
  (b) a blob this path already had in the last WQ1537_LOG_LIMIT commits of the remote
      branch — reviewed content that exists in git and is recoverable from it.
Anything else still refuses, exactly as before.

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

# How far back down the remote branch a live blob still counts as "already reviewed", and how
# many of this tool's own writes to remember per path. Both only ever widen what --apply is
# willing to overwrite, so they stay small and boring.
LOG_LIMIT = int(os.environ.get("WQ1537_LOG_LIMIT", "50"))
WRITE_HISTORY = 10
STATE_FILE = "wq1537-deploy-sync-state.json"


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


def state_path(repo):
    """Sidecar lives in the repo's git dir, not the worktree: this tool's own bookkeeping must
    never show up as a new dirty/untracked path in the tree it is deploying into."""
    return os.path.join(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip(), STATE_FILE)


def load_state(repo):
    try:
        with open(state_path(repo)) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return {"paths": {}}
    return s if isinstance(s, dict) and isinstance(s.get("paths"), dict) else {"paths": {}}


def record_write(repo, path, blob_sha, target_sha):
    """Remember that WE put `blob_sha` at `path`, so a later run reads that as our own output
    instead of as somebody's WIP. Best-effort: a repo whose git dir isn't writable still gets
    the remote-history check below, so a failure here costs recall, not correctness."""
    state = load_state(repo)
    entry = state["paths"].setdefault(path, {"written": []})
    written = [s for s in entry.get("written", []) if s != blob_sha]
    written.append(blob_sha)
    entry["written"] = written[-WRITE_HISTORY:]
    entry["target_sha"] = target_sha
    entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with open(state_path(repo), "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def remote_blob_shas(repo, ref, path, limit=LOG_LIMIT):
    """Blob shas this path carried in the last `limit` commits of the remote branch. Live
    content matching one of them landed through review at some point and is recoverable from
    git, so overwriting it loses nothing that exists nowhere else."""
    commits = git(repo, "log", f"--max-count={limit}", "--format=%H", ref, "--",
                  path).stdout.split()
    if not commits:
        return set()
    p = subprocess.run(["git", "-C", repo, "cat-file", "--batch-check"],
                       input="".join(f"{c}:{path}\n" for c in commits),
                       capture_output=True, text=True, timeout=60)
    return {fields[0] for fields in (line.split() for line in p.stdout.splitlines())
            if len(fields) >= 2 and fields[1] == "blob"}


def status(repo, path, remote, branch, fetch=True):
    if fetch:
        git(repo, "fetch", remote, branch, check=False)
    ref = remote_ref(remote, branch)
    live_path = os.path.join(repo, path)
    live_bytes = open(live_path, "rb").read() if os.path.exists(live_path) else None
    target_bytes, target_exec = blob_and_exec(repo, ref, path)
    # Dirty means THIS path differs from whatever HEAD currently is. Untracked/other-path dirt
    # (wq#1538's territory) is irrelevant to a single-file sync. Dirty alone is NOT a veto:
    # --apply never moves HEAD, so every file this tool has ever written reads as dirty
    # forever (wq#1544). Only a dirty file whose content we cannot account for — not our own
    # recorded write, not a blob from the remote branch's recent history — is a foreign edit,
    # and that is the one thing --apply refuses to clobber.
    dirty = bool(git(repo, "status", "--porcelain", "--", path).stdout.strip())
    live_sha = (git(repo, "hash-object", "--", live_path).stdout.strip()
                if live_bytes is not None else None)
    self_written = bool(live_sha) and live_sha in load_state(repo)["paths"].get(
        path, {}).get("written", [])
    in_remote_history = bool(live_sha) and live_sha in remote_blob_shas(repo, ref, path)
    return {
        "repo": repo, "path": path, "remote_ref": ref,
        "in_sync": live_bytes == target_bytes,
        "dirty": dirty,
        "self_written": self_written,
        "in_remote_history": in_remote_history,
        "foreign_edit": dirty and not (self_written or in_remote_history),
        "target_sha": git(repo, "rev-parse", ref).stdout.strip(),
        "_live_sha": live_sha,
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
    if st["foreign_edit"]:
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
    # Recorded only after the write verified, so a rolled-back attempt never grants itself
    # permission to clobber the content it failed to replace.
    final = status(repo, path, remote, branch, fetch=False)
    record_write(repo, path, final["_live_sha"], final["target_sha"])
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
              "self_written": st["self_written"], "in_remote_history": st["in_remote_history"],
              "foreign_edit": st["foreign_edit"], "changed": changed}
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        verb = "SYNCED" if changed else ("IN SYNC" if st["in_sync"] else "NOT IN SYNC")
        if st["foreign_edit"]:
            note = ", dirty (foreign edit)"
        elif st["dirty"]:
            note = ", dirty (this tool's own write)"
        else:
            note = ""
        print(f"{st['repo']}/{st['path']}: {verb} (vs {st['remote_ref']} "
              f"{st['target_sha'][:7]}{note})")
    return 0 if (st["in_sync"] or changed) else 1


if __name__ == "__main__":
    sys.exit(main())
