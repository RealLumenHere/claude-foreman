#!/usr/bin/env python3
"""wq#2620 — safe pruner for stale worktrees under /home/lumen/scratch/worktrees
and /home/lumen/worktrees.

`lumen-pr commit`/`land` never removes the linked worktree it worked in (wq#2561's
own worktree is one example, still present after landing), and fworker's per-ticket
worktrees under /home/lumen/worktrees have no cleanup step at all. 2387 directories
had accumulated across both roots as of 2026-09-25, 718M+ in scratch alone.

For every linked worktree directory found directly under the given roots, this
prunes it ONLY if all of:
  (a) `git merge-base --is-ancestor <branch> origin/<default>` — the branch tip is
      already reachable from the repo's default branch, so nothing in it is unique
      (covers both a landed branch and one abandoned before it ever diverged).
  (b) `git status --porcelain` is empty — no uncommitted or untracked changes.
  (c) it is registered in `git worktree list --porcelain` (the authoritative
      source, never inferred from the directory listing alone) and is neither
      locked nor detached.
  (d) it is not the active worktree of a live wq claim (worktree name
      `wqNNNN-aN` under /home/lumen/worktrees, ticket NNNN `status='claimed'`
      with an unexpired lease) or a live slock (lumen-pr's `.owners/<name>`
      record under scratch/worktrees, holder has an unexpired row in `slocks`
      or `slocks_ro`).
Any read failure on the liveness checks (b/d) fails CLOSED — treated as live,
never pruned — since an unreadable answer is not evidence of a safe one.

    prune_worktrees.py                    # dry run, human summary
    prune_worktrees.py --json             # dry run, full JSON to stdout
    prune_worktrees.py --apply            # actually `git worktree remove`
    prune_worktrees.py --no-fetch         # skip `git fetch --prune`, use local refs
    prune_worktrees.py --roots R [R ...]  # override the default two roots

Every run appends one JSON line to --log (default below) regardless of mode, so a
dry run and the apply run that follows it both leave a paper trail.
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

DEFAULT_ROOTS = ["/home/lumen/scratch/worktrees", "/home/lumen/worktrees"]
WQ_DB = os.environ.get("PRUNE_WQ_DB", "/home/lumen/agents/work-queue/queue.db")
LOG_PATH = os.environ.get("PRUNE_LOG", "/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl")

# fworker's per-ticket shape (harness/fworker.py, bin/wq FWORKER_IDENTITY_RE) — a
# worktree named this way maps straight to a wq ticket id, no owner file needed.
FWORKER_WT_RE = re.compile(r"^wq(\d+)-a\d+$")

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def sh(args, timeout=120):
    """(returncode, stdout, stderr) for `args`, or (1, "", err) on any failure —
    a single misbehaving git call must never abort the whole sweep."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def _parse_ts(value):
    try:
        return datetime.datetime.strptime(value, _TS_FMT)
    except (TypeError, ValueError):
        return None


def _now_utc_naive():
    """Naive UTC now, comparable with `_parse_ts` output (also naive — the stored
    timestamps carry no tzinfo of their own)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


# ── discovery ────────────────────────────────────────────────────────────────

def linked_worktree_repo(path):
    """Repo root a linked worktree at `path` belongs to, or None if `path` is not
    one — no `.git` file (stray directory), or `.git` is a DIRECTORY (a primary
    checkout, which this tool never touches, only ever a linked worktree)."""
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
    gitdir = line.split("gitdir:", 1)[1].strip()
    marker = "/.git/worktrees/"
    if marker not in gitdir:
        return None
    return gitdir.split(marker, 1)[0]


def discover_worktrees(roots):
    """{repo_root: [worktree_path, ...]} for every linked worktree directory found
    directly under `roots`. Bookkeeping entries (`.owners`) and anything that
    isn't a linked worktree are skipped, never guessed at."""
    by_repo = {}
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            repo = linked_worktree_repo(path)
            if repo is None:
                continue
            by_repo.setdefault(repo, []).append(path)
    return by_repo


# ── git state ────────────────────────────────────────────────────────────────

def worktree_registry(repo):
    """path -> {branch, detached, locked} from `git worktree list --porcelain` —
    criterion (c), the authoritative registry, never inferred from the directory
    listing alone."""
    rc, out, _ = sh(["git", "-C", repo, "worktree", "list", "--porcelain"])
    if rc != 0:
        return {}
    entries, cur, path = {}, None, None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
            cur = {"branch": None, "detached": False, "locked": False}
            entries[path] = cur
        elif cur is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
    return entries


def default_branch(repo):
    """The repo's default branch name (no remote URL touched — instinct 4)."""
    rc, out, _ = sh(["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if rc == 0 and out.strip():
        return out.strip().rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        rc, _, _ = sh(["git", "-C", repo, "show-ref", "--verify", "--quiet",
                       f"refs/remotes/origin/{cand}"])
        if rc == 0:
            return cand
    return None


def is_clean(path):
    rc, out, _ = sh(["git", "-C", path, "status", "--porcelain"])
    return rc == 0 and out.strip() == ""


def is_merged(repo, branch, default):
    """True iff `branch`'s tip is an ancestor of origin/<default> — deliberately
    NOT a commit count, so a squash-merged branch (tip not reachable from
    origin/default even though its content landed) reports False and is left
    alone rather than guessed safe. Conservative in the direction the ticket
    asks for: 'do NOT touch a worktree with unmerged commits'."""
    rc, _, _ = sh(["git", "-C", repo, "merge-base", "--is-ancestor", branch, f"origin/{default}"])
    return rc == 0


# ── liveness (criterion (b): not a live wq claim or slock) ──────────────────

def _db_conn():
    if not os.path.exists(WQ_DB):
        return None
    return sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True, timeout=10)


def wq_claim_is_live(ticket_id):
    """(is_live, reason) for a /home/lumen/worktrees/wqNNNN-aN worktree, straight
    off the ticket id in its own name. A claimed ticket with no lease_expires is
    treated as live indefinitely (no evidence it ever ends), matching the fail-
    closed rule below."""
    conn = _db_conn()
    if conn is None:
        return True, "wq db not found — fail closed"
    try:
        row = conn.execute("SELECT status, lease_expires FROM tasks WHERE id=?",
                            (ticket_id,)).fetchone()
    except Exception as e:
        return True, f"wq db unreadable ({e}) — fail closed"
    finally:
        conn.close()
    if row is None:
        return False, "no such ticket in wq db"
    status, lease_expires = row
    if status != "claimed":
        return False, f"status={status}"
    if lease_expires:
        exp = _parse_ts(lease_expires)
        if exp is not None and exp < _now_utc_naive():
            return False, f"claimed but lease expired {lease_expires}"
    return True, f"status=claimed, lease={lease_expires or 'none'}"


def scratch_owner(scratch_root, wt_name):
    owner_file = os.path.join(scratch_root, ".owners", wt_name)
    try:
        with open(owner_file) as f:
            for line in f:
                if line.startswith("agent="):
                    return line.strip().split("=", 1)[1] or None
    except OSError:
        return None
    return None


def slock_is_live(agent):
    """(is_live, reason) for the lumen-pr owner-agent of a scratch/worktrees
    worktree, by its holds in `slocks` (exclusive) and `slocks_ro` (shared) —
    the two tables bin/slock and bin/wq both write to."""
    conn = _db_conn()
    if conn is None:
        return True, "wq db not found — fail closed"
    try:
        rows = conn.execute(
            "SELECT expires_at FROM slocks WHERE holder=? "
            "UNION ALL SELECT expires_at FROM slocks_ro WHERE holder=?",
            (agent, agent)).fetchall()
    except Exception as e:
        return True, f"slock db unreadable ({e}) — fail closed"
    finally:
        conn.close()
    for (expires_at,) in rows:
        if not expires_at:
            return True, f"slock held by {agent}, no expiry"
        exp = _parse_ts(expires_at)
        if exp is None or exp > _now_utc_naive():
            return True, f"slock held by {agent}, expires {expires_at}"
    return False, f"owner={agent}, no live slock"


# ── classification ───────────────────────────────────────────────────────────

def classify(path, repo, registry, default, scratch_root):
    """(action, reason) — action is 'prune' or 'skip'. Every skip path is a
    distinct, logged reason; nothing falls through silently."""
    info = registry.get(path)
    if info is None:
        return "skip", "not registered in `git worktree list` (out of scope or already gone)"
    if info["locked"]:
        return "skip", "worktree is locked"
    if info["detached"] or not info["branch"]:
        return "skip", "detached HEAD, cannot compare to a default branch"
    if default is None:
        return "skip", "could not resolve origin's default branch"
    if not is_clean(path):
        return "skip", "uncommitted or untracked changes present"
    if not is_merged(repo, info["branch"], default):
        return "skip", f"branch {info['branch']!r} has commits not in origin/{default}"

    name = os.path.basename(path)
    m = FWORKER_WT_RE.match(name)
    if m:
        live, why = wq_claim_is_live(int(m.group(1)))
        return ("skip", f"live wq claim ({why})") if live else \
               ("prune", f"merged into origin/{default}, clean, {why}")
    if scratch_root:
        agent = scratch_owner(scratch_root, name)
        if agent:
            live, why = slock_is_live(agent)
            return ("skip", f"live slock ({why})") if live else \
                   ("prune", f"merged into origin/{default}, clean, {why}")
        return "prune", f"merged into origin/{default}, clean, no owner record"
    return "prune", f"merged into origin/{default}, clean, no liveness record to check"


# ── size / logging ───────────────────────────────────────────────────────────

def dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
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
    record = dict(record, ts=time.strftime(_TS_FMT, time.gmtime()))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ── run ──────────────────────────────────────────────────────────────────────

def scratch_root_for(path, roots):
    for r in roots:
        if os.path.basename(r.rstrip("/")) == "worktrees" and "/scratch/" in r \
                and path.startswith(r + os.sep):
            return r
    return None


def run(roots, apply, fetch):
    by_repo = discover_worktrees(roots)
    results = []
    for repo, paths in sorted(by_repo.items()):
        if fetch:
            sh(["git", "-C", repo, "fetch", "-q", "--prune", "origin"], timeout=180)
        default = default_branch(repo)
        registry = worktree_registry(repo)
        for path in paths:
            action, reason = classify(path, repo, registry, default,
                                       scratch_root_for(path, roots))
            rec = {"path": path, "repo": repo, "action": action, "reason": reason}
            if action == "prune":
                # Measured BEFORE any removal — in --apply mode the directory is
                # gone by the time a caller could look at it again.
                rec["bytes"] = dir_size(path)
                if apply:
                    rc, _, err = sh(["git", "-C", repo, "worktree", "remove", path])
                    if rc == 0:
                        rec["removed"] = True
                    else:
                        rec["action"] = "skip"
                        rec["removed"] = False
                        rec["reason"] = f"git worktree remove failed: {err.strip()[:300]}"
            results.append(rec)
        sh(["git", "-C", repo, "worktree", "prune", "-v"] +
           ([] if apply else ["--dry-run"]))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roots", nargs="+", default=DEFAULT_ROOTS)
    ap.add_argument("--apply", action="store_true",
                     help="actually run `git worktree remove`; default is dry-run")
    ap.add_argument("--no-fetch", action="store_true",
                     help="skip `git fetch --prune origin`; use current local refs")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args(argv)

    results = run(args.roots, args.apply, fetch=not args.no_fetch)
    pruned = [r for r in results if r["action"] == "prune"]
    size_total = sum(r.get("bytes", 0) for r in pruned)

    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "roots": args.roots,
        "scanned": len(results),
        "prunable": len(pruned),
        "skipped": len(results) - len(pruned),
        "bytes": size_total,
    }
    append_log(args.log, {"summary": summary, "results": results})

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        print(f"{summary['mode']}: scanned={summary['scanned']} "
              f"prunable={summary['prunable']} skipped={summary['skipped']} "
              f"size={human(size_total)}")
        for r in pruned:
            tag = "REMOVED" if r.get("removed") else "PRUNE"
            print(f"  {tag} {r['path']} ({human(r['bytes'])}) — {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
