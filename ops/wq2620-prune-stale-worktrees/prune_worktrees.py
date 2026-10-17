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
  (e) it LANDED at least `--grace-days` ago (default 7, wq#2909) — a merge gets
      one week to be reverted before its worktree is gone for good. "Landed"
      means the date of the commit that brought the branch onto the default
      branch, or the wq ticket's `done_at` for a `wqNNNN-aN` worktree —
      whichever is later. Not the branch tip's own commit date: that is only a
      lower bound on landing time, so a branch committed long before it merged
      got no grace period at all (wq#2934 found this, wq#2942 fixed it).
Any read failure on the liveness checks (b/d) or the age check (e) fails
CLOSED — treated as live/too-new, never pruned — since an unreadable answer is
not evidence of a safe one.

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

# Two formats, deliberately separate (wq#2669). `_DB_TS_FMT` is what the wq
# database actually stores: bin/wq:145 and bin/slock:43 both write every
# `tasks.lease_expires` and `slocks(_ro).expires_at` with this constant. It is
# not ISO-8601 — no `T`, no `Z` — and parsing db values with the log format is
# the bug wq#2644 found: every real value failed to parse, so no lease or slock
# ever read as expired. `_LOG_TS_FMT` is ours alone, for the JSONL run log.
_DB_TS_FMT = "%Y-%m-%d %H:%M:%S"
_LOG_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


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
        return datetime.datetime.strptime(value, _DB_TS_FMT)
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
    """True iff `branch`'s tip is an ancestor of origin/<default>. Deliberately
    NOT a commit count, so a squash-merged branch (tip not reachable from
    origin/default even though its content landed) reports False here — that
    case is caught separately by `squash_landing_commit` below, not guessed
    safe by this ancestor test alone."""
    rc, _, _ = sh(["git", "-C", repo, "merge-base", "--is-ancestor", branch, f"origin/{default}"])
    return rc == 0


def _patch_id(repo, diff_args):
    """Stable patch-id for `git diff <diff_args>`, or None if either command
    fails or there is no diff (an empty commit has no patch-id to match)."""
    rc, diff_out, _ = sh(["git", "-C", repo, "diff", *diff_args])
    if rc != 0 or not diff_out.strip():
        return None
    # patch-id reads stdin, so feed it diff_out directly rather than relying on sh()'s argv path.
    r = subprocess.run(["git", "-C", repo, "patch-id", "--stable"],
                        input=diff_out, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0]


def squash_landing_commit(repo, branch, default):
    """The commit on origin/<default> that is a squash-equivalent of `branch`,
    or None if there isn't one — covers wq#4105: a squash-merge-then-delete
    workflow leaves `branch`'s tip unreachable from origin/<default> forever
    (its own commits were never replayed there, only a new squashed commit
    with the same total diff), so `is_merged`'s ancestor test never matches
    and the worktree sits stale indefinitely (164 of 241 found 2026-10-16).

    Compares the patch-id of `branch`'s entire diff since its merge-base
    against the patch-id of each individual commit origin/<default> gained
    since that same merge-base — a squash commit's diff is, by construction,
    the sum of the branch's commits, so the patch-ids match even though no
    commit object is shared. Any unmatched or unreadable diff returns None,
    never guessed as a match."""
    rc, mb, _ = sh(["git", "-C", repo, "merge-base", branch, f"origin/{default}"])
    if rc != 0 or not mb.strip():
        return None
    mb = mb.strip()
    branch_pid = _patch_id(repo, [f"{mb}..{branch}"])
    if branch_pid is None:
        return None
    rc, out, _ = sh(["git", "-C", repo, "rev-list", f"{mb}..origin/{default}"])
    if rc != 0:
        return None
    for commit in out.split():
        if _patch_id(repo, [f"{commit}^", commit]) == branch_pid:
            return commit
    return None


def commit_ts(repo, rev):
    """Naive-UTC committer date of `rev`, or None if it can't be read."""
    rc, out, _ = sh(["git", "-C", repo, "log", "-1", "--format=%ct", rev])
    if rc != 0 or not out.strip():
        return None
    try:
        return datetime.datetime.fromtimestamp(
            int(out.strip()), datetime.timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return None


def landing_commit(repo, branch, default):
    """The commit that brought `branch` onto origin/<default>: the oldest commit
    that both descends from `branch`'s tip and is reachable from
    origin/<default>. For a `--no-ff` merge that is the merge commit itself; for
    a fast-forward, where no merge commit exists anywhere, it is the next commit
    pushed onto the default branch — later than the true landing, so erring
    toward more protection, not less.

    None when the range is empty: `branch`'s tip IS origin/<default>'s tip, and
    git then holds no record at all of when it got there (a fast-forward writes
    no commit of its own). That case falls back to `done_at` below."""
    rc, out, _ = sh(["git", "-C", repo, "rev-list", "--ancestry-path",
                      f"{branch}..origin/{default}"])
    if rc != 0:
        return None
    revs = out.split()
    return revs[-1] if revs else None


def wq_done_at(ticket_id):
    """Naive-UTC `tasks.done_at` for a ticket, or None if there is no row, no
    `done_at` yet, or the db can't be read. bin/wq:363 writes it with `utcnow()`
    in `_DB_TS_FMT`, the same format every other timestamp here is parsed with.

    A None here is "this source has nothing to say", not "prune it" — an
    unreadable db still fails closed, because `wq_claim_is_live` treats exactly
    that condition as a live claim before `classify` can ever return prune."""
    conn = _db_conn()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT done_at FROM tasks WHERE id=?",
                            (ticket_id,)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    return _parse_ts(row[0]) if row else None


def landing_age_days(repo, branch, default, wt_name=None, squash_commit=None):
    """(days_since_landing, source) — how long ago `branch`'s work became
    reachable from origin/<default> — or (None, why) when nothing readable says.
    Fails closed at the call site: an unknown age is never old enough.

    wq#2909 added this grace period so a same-day merge keeps its worktree for a
    week. wq#2934 proved it measured the wrong clock: it read the branch TIP's
    own commit date, and a commit date is only a LOWER bound on landing time. A
    branch whose work was committed ten days before it finally merged — blocked,
    on hold, or reopened, 50 of the last 2000 done tickets in the wq db — read
    as ten days old and was prunable the instant the merge landed. Zero revert
    window: precisely the bug wq#2909 was filed against, reached by another
    route.

    So take the LATEST timestamp any source can evidence, which is the smallest
    age and so the most protection:
      * the landing commit's date — git's only record of the merge event;
      * `tasks.done_at` for a `wqNNNN-aN` fworker worktree — the fleet's record
        of when its ticket actually closed, and the only source that covers the
        one case git cannot (a fast-forward still sitting at the default
        branch's tip);
      * the branch tip's commit date — wq#2934's clock, demoted to the floor it
        always was. Being <= the other two it never wins the max; it is only
        what remains for a branch that never diverged, where there was no
        landing event to date in the first place."""
    cands = []
    lc = squash_commit or landing_commit(repo, branch, default)
    if lc:
        ts = commit_ts(repo, lc)
        if ts:
            cands.append((ts, f"landing commit {lc[:8]}"))
    m = FWORKER_WT_RE.match(wt_name or "")
    if m:
        ts = wq_done_at(int(m.group(1)))
        if ts:
            cands.append((ts, f"wq#{m.group(1)} done_at"))
    ts = commit_ts(repo, branch)
    if ts:
        cands.append((ts, "branch tip commit, no landing event on record"))
    if not cands:
        return None, "no landing commit, no wq done_at, unreadable tip commit date"
    ts, source = max(cands, key=lambda c: c[0])
    return (_now_utc_naive() - ts).total_seconds() / 86400.0, source


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
        if exp is None:
            return True, f"status=claimed, lease={lease_expires} unparseable — fail closed"
        if exp < _now_utc_naive():
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
        if exp is None:
            return True, f"slock held by {agent}, expires {expires_at} unparseable — fail closed"
        if exp > _now_utc_naive():
            return True, f"slock held by {agent}, expires {expires_at}"
    return False, f"owner={agent}, no live slock"


# ── classification ───────────────────────────────────────────────────────────

def classify(path, repo, registry, default, scratch_root, grace_days, backup_root=None):
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

    branch = info["branch"]
    merged = is_merged(repo, branch, default)
    squash_commit = None
    merge_desc = f"merged into origin/{default}"
    if not merged:
        squash_commit = squash_landing_commit(repo, branch, default)
        if squash_commit is None:
            return "skip", f"branch {branch!r} has commits not in origin/{default}"
        merge_desc = f"squash-equivalent of origin/{default}@{squash_commit[:8]}"

    clean = is_clean(path)
    backup_path = None
    if not clean:
        if backup_root is None:
            return "skip", "uncommitted or untracked changes present"
        # wq#4105: a dirty worktree whose branch has already landed (plain or
        # squash) is not "someone's in-progress work" the way an unmerged one
        # is — it is a merged worktree with leftover scratch (build caches,
        # stray edits). Back the diff up before pruning rather than letting it
        # sit stale forever (42 of 241 found 2026-10-16 were exactly this).
        backup_path, err = backup_dirty_worktree(path, backup_root)
        if backup_path is None:
            return "skip", f"uncommitted changes present, backup failed ({err})"

    name = os.path.basename(path)
    age, age_src = landing_age_days(repo, branch, default, name, squash_commit=squash_commit)
    if age is None:
        return "skip", (f"branch {branch!r} landing date unreadable "
                         f"({age_src}) — fail closed")
    if age < grace_days:
        return "skip", (f"{merge_desc}, only {age:.1f}d since landing "
                         f"per {age_src} (grace period {grace_days}d)")

    clean_note = "clean" if clean else f"dirty, backed up to {backup_path}"
    m = FWORKER_WT_RE.match(name)
    if m:
        live, why = wq_claim_is_live(int(m.group(1)))
        return ("skip", f"live wq claim ({why})") if live else \
               ("prune", f"{merge_desc}, {clean_note}, {why}")
    if scratch_root:
        agent = scratch_owner(scratch_root, name)
        if agent:
            live, why = slock_is_live(agent)
            return ("skip", f"live slock ({why})") if live else \
                   ("prune", f"{merge_desc}, {clean_note}, {why}")
        return "prune", f"{merge_desc}, {clean_note}, no owner record"
    return "prune", f"{merge_desc}, {clean_note}, no liveness record to check"


DEFAULT_BACKUP_ROOT = "/home/lumen/backups/worktree-prune-auto"


def backup_dirty_worktree(path, backup_root):
    """Write `path`'s tracked diff and untracked files under `backup_root` as
    `<worktree-name>-<timestamp>/{diff.patch, untracked.tgz}`, matching the
    by-hand backup-then-delete done for wq#4105's 241 stale worktrees
    (MANIFEST.tsv at /home/lumen/backups/worktree-prune-20261016). Returns
    (backup_dir, None) on success, (None, error) on any failure — a worktree
    whose backup fails is never pruned, per the caller's fail-closed contract."""
    name = os.path.basename(path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = os.path.join(backup_root, f"{name}-{stamp}")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        return None, str(e)

    rc, diff_out, err = sh(["git", "-C", path, "diff", "HEAD"])
    if rc != 0:
        return None, f"git diff failed: {err.strip()[:200]}"
    try:
        with open(os.path.join(out_dir, "diff.patch"), "w") as f:
            f.write(diff_out)
    except OSError as e:
        return None, str(e)

    rc, untracked_out, err = sh(["git", "-C", path, "ls-files",
                                  "--others", "--exclude-standard"])
    if rc != 0:
        return None, f"git ls-files failed: {err.strip()[:200]}"
    untracked = [u for u in untracked_out.splitlines() if u]
    if untracked:
        tgz = os.path.join(out_dir, "untracked.tgz")
        rc, _, err = sh(["tar", "-C", path, "-czf", tgz, *untracked], timeout=180)
        if rc != 0:
            return None, f"tar of untracked files failed: {err.strip()[:200]}"
    return out_dir, None


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
    record = dict(record, ts=time.strftime(_LOG_TS_FMT, time.gmtime()))
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


def run(roots, apply, fetch, grace_days=7, backup_root=DEFAULT_BACKUP_ROOT):
    by_repo = discover_worktrees(roots)
    results = []
    for repo, paths in sorted(by_repo.items()):
        if fetch:
            sh(["git", "-C", repo, "fetch", "-q", "--prune", "origin"], timeout=180)
        default = default_branch(repo)
        registry = worktree_registry(repo)
        for path in paths:
            action, reason = classify(path, repo, registry, default,
                                       scratch_root_for(path, roots), grace_days,
                                       backup_root=backup_root)
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
    ap.add_argument("--grace-days", type=float, default=7,
                     help="wq#2909: only prune a merged+clean worktree once it "
                          "LANDED at least this many days ago — landing commit "
                          "date or wq done_at, whichever is later, never the "
                          "branch tip's own commit date (wq#2942; default 7)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=LOG_PATH)
    ap.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT,
                     help="wq#4105: where a dirty-but-merged worktree's diff "
                          "and untracked files are backed up before pruning")
    ap.add_argument("--no-backup", action="store_true",
                     help="skip dirty worktrees entirely instead of backing "
                          "them up (old behavior)")
    args = ap.parse_args(argv)

    results = run(args.roots, args.apply, fetch=not args.no_fetch,
                  grace_days=args.grace_days,
                  backup_root=None if args.no_backup else args.backup_root)
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
