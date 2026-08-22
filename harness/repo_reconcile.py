#!/usr/bin/env python3
"""repo_reconcile — reconcile a managed repo whose local branch diverged from a
PR-only remote. wq#686.

The failure this exists for: work gets committed straight onto the local `main` of a
deployed repo (`/home/lumen/pokemon`), the same content later lands on the remote as ONE
squashed PR commit, and the two histories are now permanently unrelatable. `git push` is
refused — the class-a-main ruleset demands PR + squash + linear history — and `git pull`
would merge, which the ruleset also forbids. wq#686 sat in that state for three days with
seven unpushed commits.

The way out is nearly always cheap, because the squashed PR commit already carries the
identical content: move the local ref onto the remote ref and keep the working tree
exactly where it is. What makes that safe is one mechanical fact, not a judgement call —

    tree(local branch) == tree(remote branch)

Byte-identical committed content is the proof of "deploy parity" that lets the ref move
without deploying anything: the reset changes no file, so a process running out of that
tree cannot notice. This tool refuses to act unless it can show that equality, which is
the whole point — it turns "is it safe to reset the live tree?" from a question you have
to reason about into one you can check.

Invariants it holds:
  1. Reset only on proven tree equality. Otherwise the content genuinely differs and the
     branch->PR->squash path (or a Frank ruleset bypass) is the only honest answer.
  2. Soft reset, never --hard. Uncommitted and untracked work in a live tree survives.
  3. Archive the abandoned SHAs under a tag first, so the old history stays reachable.
  4. Idempotent. Re-running against an already-reconciled repo is a no-op success.

Usage:
    repo_reconcile.py status [--repo PATH] [--branch main] [--remote origin] [--no-fetch]
    repo_reconcile.py reconcile [--repo PATH] --apply [--tag NAME]

Exit codes (greppable from cron/self-heal, 0 only ever means "nothing to chase"):
    0   IN_SYNC          local == remote, or --apply reconciled it
    10  SAFE_RESET       ahead, but content-identical: fixable by this tool with --apply
    20  CONTENT_DIVERGED ahead with different content: needs PR->squash, or Frank
    30  BEHIND_ONLY      remote ahead: needs a deploy, then a fast-forward
    2   error
"""
import argparse
import os
import subprocess
import sys
import time

IN_SYNC, SAFE_RESET, CONTENT_DIVERGED, BEHIND_ONLY, ERROR = (
    "IN_SYNC", "SAFE_RESET", "CONTENT_DIVERGED", "BEHIND_ONLY", "ERROR")

EXIT = {IN_SYNC: 0, SAFE_RESET: 10, CONTENT_DIVERGED: 20, BEHIND_ONLY: 30, ERROR: 2}

DEFAULT_REPO = os.environ.get("FOREMAN_RECONCILE_REPO", "/home/lumen/pokemon")


class GitError(RuntimeError):
    pass


def git(repo, *args, check=True):
    """Always `git -C repo` — never chdir, so one process can sweep several repos."""
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
    return p.stdout.strip()


def counts(repo, local, remote_ref):
    """(behind, ahead) relative to the remote ref."""
    out = git(repo, "rev-list", "--left-right", "--count", f"{remote_ref}...{local}")
    behind, ahead = out.split()
    return int(behind), int(ahead)


def inspect(repo, branch="main", remote="origin", fetch=True):
    remote_ref = f"{remote}/{branch}"
    if fetch:
        git(repo, "fetch", remote, check=False)
    behind, ahead = counts(repo, branch, remote_ref)
    local_tree = git(repo, "rev-parse", f"{branch}^{{tree}}")
    remote_tree = git(repo, "rev-parse", f"{remote_ref}^{{tree}}")
    if ahead == 0:
        verdict = IN_SYNC if behind == 0 else BEHIND_ONLY
    else:
        verdict = SAFE_RESET if local_tree == remote_tree else CONTENT_DIVERGED
    return {
        "repo": repo, "branch": branch, "remote_ref": remote_ref,
        "ahead": ahead, "behind": behind,
        "local_head": git(repo, "rev-parse", branch),
        "remote_head": git(repo, "rev-parse", remote_ref),
        "local_tree": local_tree, "remote_tree": remote_tree,
        "verdict": verdict,
    }


def explain(st):
    v = st["verdict"]
    if v == IN_SYNC:
        return f"{st['branch']} == {st['remote_ref']} at {st['local_head'][:7]}. Nothing to do."
    if v == BEHIND_ONLY:
        return (f"{st['remote_ref']} is {st['behind']} ahead of local {st['branch']}. A "
                f"fast-forward REWRITES FILES in this tree — deploy the remote content "
                f"first, then `git merge --ff-only {st['remote_ref']}`. Not this tool's job.")
    if v == SAFE_RESET:
        return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} but the trees "
                f"are identical ({st['local_tree'][:7]}) — the remote already carries this "
                f"content, squashed. `reconcile --apply` archives the local SHAs and moves "
                f"the ref; zero files change.")
    return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} and the content "
            f"DIFFERS (local {st['local_tree'][:7]} vs remote {st['remote_tree'][:7]}). No "
            f"safe ref move exists: branch off {st['branch']}, open a PR, squash it, and "
            f"reconcile afterwards — or get a ruleset bypass from Frank.")


def reconcile(repo, branch="main", remote="origin", fetch=True, apply=False, tag=None):
    st = inspect(repo, branch, remote, fetch)
    if st["verdict"] != SAFE_RESET or not apply:
        return st
    if git(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise GitError(f"{repo} has {branch} un-checked-out; refusing to move a ref out from "
                       "under a different HEAD")
    tag = tag or f"archive/reconcile-{branch}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    git(repo, "tag", "-a", tag, st["local_head"], "-m",
        f"pre-reconcile snapshot of {branch} ({st['ahead']} unpushed commits); content "
        f"identical to {st['remote_ref']} {st['remote_head'][:7]} (tree {st['local_tree']})")
    # --soft, never --hard: index and working tree are left untouched, so uncommitted edits
    # and untracked files in a live deployment tree survive the reconcile intact.
    git(repo, "reset", "--soft", st["remote_ref"])
    after = inspect(repo, branch, remote, fetch=False)
    if after["verdict"] != IN_SYNC:
        raise GitError(f"reset left {repo} at {after['verdict']} — expected IN_SYNC")
    after["archived_as"] = tag
    after["archived_head"] = st["local_head"]
    after["reset_from_ahead"] = st["ahead"]
    return after


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["status", "reconcile"])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--apply", action="store_true", help="reconcile only: actually move the ref")
    ap.add_argument("--tag", help="archive tag name (default: archive/reconcile-<branch>-<utc>)")
    a = ap.parse_args(argv)

    try:
        if a.action == "status":
            st = inspect(a.repo, a.branch, a.remote, fetch=not a.no_fetch)
        else:
            st = reconcile(a.repo, a.branch, a.remote, fetch=not a.no_fetch,
                           apply=a.apply, tag=a.tag)
    except GitError as e:
        print(f"repo_reconcile: {e}", file=sys.stderr)
        return EXIT[ERROR]

    print(f"{st['verdict']} {a.repo} {st['branch']} ahead={st['ahead']} behind={st['behind']}")
    if "archived_as" in st:
        print(f"archived {st['reset_from_ahead']} commits at {st['archived_head'][:7]} "
              f"as tag {st['archived_as']}")
    print(explain(st))
    return EXIT[st["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
