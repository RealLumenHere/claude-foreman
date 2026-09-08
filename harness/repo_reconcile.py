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
identical content: rewind the local ref past the duplicates and keep the working tree
exactly where it is. What makes that safe is a mechanical fact, not a judgement call.
There are two of them, and which one applies depends on whether the remote has moved on:

    tree(local branch) == tree(remote branch)                          -> SAFE_RESET

Byte-identical committed content is the proof of "deploy parity" that lets the ref move
straight onto the remote: the reset changes no file, so a process running out of that
tree cannot notice.

    for every path the ahead commits touched since the merge-base,
    blob(local:path) == blob(remote:path)                              -> REDUNDANT_AHEAD

Once the remote carries later commits the trees can never match again, and wq#975 is what
that costs: /home/lumen/bin sat 1 ahead / 4 behind on a duplicate of an already-landed
one-line fix, whole-tree equality could not hold, and every landed PR stopped reaching the
deployed path. The narrower proof still holds there — the duplicate commits are provably
content-free against the remote, so rewinding to the merge-base drops them without
changing a file. That leaves the repo BEHIND_ONLY: fast-forwardable again, which is all
this tool ever promises.

    for every path the ahead commits touched since the merge-base,
    blob(local:path) == blob(<some other ref on the remote>:path)        -> PR_PENDING

The third proof is what makes CONTENT_DIVERGED's own prescribed remedy executable. That
remedy ends "...open a PR, squash it, and reconcile afterwards", but wq#1472 found the
"afterwards" unreachable: `lumen-pr land` refuses to merge a PR touching a path the
primary tree holds in an unpushed commit (its wq#771 guard), and that unpushed commit is
exactly what the PR is carrying. Landing needs the rewind; the rewind needed the landing.
Two tickets deadlocked there (ISSUE-LOG #217, #218) before the loop was named.

What breaks it is that "unpushed" stops being true the moment the PR branch is pushed.
The remote then carries the content — just not on `main` yet — so rewinding the local ref
destroys nothing: the work is on the forge, in flight, and reachable. Note this proof
compares CONTENT, not SHAs: rebasing the commit onto the remote tip to keep the PR honest
rewrites its SHA, so `--contains` would answer no on a branch that demonstrably holds the
work. Requiring one single ref to match at EVERY touched path is what keeps it a proof
and not a coincidence — a ref that carries the ahead commits' whole contribution.

A ref that has the local branch as an ANCESTOR qualifies too, and is checked first: it
carries those commits whole, which preserves strictly more than matching their blobs.
Without it a PR that BUILDS on the unpushed commits and then edits a file they touched
could never qualify — its version is a superset, so the blobs differ (wq#1479; wq#1472
built this proof against a rebased PR that only added files).

Unlike the other two this one does NOT promise "zero files change": the rewind drops
content the remote has not landed yet, so the tree loses it until the PR merges. That is
the ordinary state of unmerged work, and the archive tag holds the old head regardless.

Invariants it holds:
  1. Reset only on proven content preservation — whole-tree, per-touched-path against the
     tracked branch, or per-touched-path against another ref the remote already holds.
     Otherwise the ahead commits carry work that exists nowhere else, and the
     branch->PR->squash path (or a Frank ruleset bypass) is the only honest answer.
  2. Soft reset, never --hard. Uncommitted and untracked work in a live tree survives.
     PR_PENDING additionally restores the touched paths — a soft reset alone would leave
     the dropped content staged, which blocks the land it exists to unblock — and refuses
     outright if any of those paths holds an uncommitted edit, since that edit is the one
     thing no PR branch is carrying. Every other path, and every untracked file, is
     still left alone.
  3. Archive the abandoned SHAs under a tag first, so the old history stays reachable.
  4. Idempotent. Re-running against an already-reconciled repo is a no-op success.

Usage:
    repo_reconcile.py status [--repo PATH] [--branch main] [--remote origin] [--no-fetch]
    repo_reconcile.py reconcile [--repo PATH] --apply [--tag NAME]

Exit codes (greppable from cron/self-heal, 0 only ever means "nothing to chase"):
    0   IN_SYNC          local == remote, or --apply reconciled it
    10  SAFE_RESET       ahead, but content-identical: fixable by this tool with --apply
    11  REDUNDANT_AHEAD  ahead AND behind, but the ahead commits duplicate landed content:
                         --apply rewinds to the merge-base and leaves BEHIND_ONLY
    12  PR_PENDING       ahead with content the tracked branch lacks, but another remote ref
                         (an open PR's branch) already carries it: --apply rewinds to the
                         merge-base so the PR can land, and leaves BEHIND_ONLY
    20  CONTENT_DIVERGED ahead with different content: needs PR->squash, or Frank
    30  BEHIND_ONLY      remote ahead: needs a deploy, then a fast-forward
    2   error
"""
import argparse
import os
import subprocess
import sys
import time

IN_SYNC, SAFE_RESET, REDUNDANT_AHEAD, CONTENT_DIVERGED, BEHIND_ONLY, ERROR = (
    "IN_SYNC", "SAFE_RESET", "REDUNDANT_AHEAD", "CONTENT_DIVERGED", "BEHIND_ONLY", "ERROR")
PR_PENDING = "PR_PENDING"

EXIT = {IN_SYNC: 0, SAFE_RESET: 10, REDUNDANT_AHEAD: 11, PR_PENDING: 12,
        CONTENT_DIVERGED: 20, BEHIND_ONLY: 30, ERROR: 2}

DEFAULT_REPO = os.environ.get("FOREMAN_RECONCILE_REPO", "/home/lumen/pokemon")


class GitError(RuntimeError):
    pass


def git(repo, *args, check=True):
    """Always `git -C repo` — never chdir, so one process can sweep several repos."""
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
    return p.stdout.strip()


def git_ok(repo, *args):
    """For the git commands whose ANSWER is the exit code (`merge-base --is-ancestor`),
    where `git()` would hand back an empty stdout that reads as success either way."""
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          timeout=120).returncode == 0


def counts(repo, local, remote_ref):
    """(behind, ahead) relative to the remote ref."""
    out = git(repo, "rev-list", "--left-right", "--count", f"{remote_ref}...{local}")
    behind, ahead = out.split()
    return int(behind), int(ahead)


def redundancy(repo, base, local, remote_ref):
    """(paths the ahead commits touched, does the remote already carry all of them?).

    The per-path proof behind REDUNDANT_AHEAD. Whole-tree equality answers "would this
    reset change any file at all?", which is unanswerable once the remote has later
    commits. This asks the question that still has an answer: of the paths the ahead
    commits are responsible for, is there one whose content the remote lacks? If not,
    those commits are duplicates of landed work and rewinding past them deploys nothing.
    --no-renames keeps the pathspec literal, so a rename heuristic can never quietly
    narrow what gets proven.
    """
    touched = [p for p in git(repo, "diff", "--name-only", "--no-renames",
                              base, local).splitlines() if p]
    if not touched:
        return touched, True
    unmatched = git(repo, "diff", "--name-only", "--no-renames", local, remote_ref,
                    "--", *touched)
    return touched, not unmatched


def preserved_on(repo, local, touched, remote, exclude):
    """The remote ref that already carries the ahead commits' content, or "" if none.

    The PR_PENDING proof. Same per-path question `redundancy` asks, aimed at every OTHER
    ref the remote has rather than only the tracked branch: is there one ref against which
    the local branch has nothing outstanding at any touched path? If so the ahead commits'
    entire contribution is already on the forge and rewinding past them loses nothing.

    One ref must satisfy every path. Testing paths independently would let two unrelated
    branches each account for a fragment and together "prove" a state neither holds, which
    is how a coincidence gets mistaken for a pushed PR. origin/HEAD is skipped as a
    symbolic alias for the tracked branch — matching it would only restate `redundancy`.

    A ref that has the local branch as an ANCESTOR is accepted first and on stronger
    grounds: it carries every ahead commit whole, so preservation needs no per-path
    argument at all. The blob comparison is the fallback for the PR that rebased or
    squashed those commits and no longer contains them by SHA. Without the ancestor case a
    PR that MODIFIES a path its own base commits touched can never qualify — its version
    is a superset, so the blobs differ — which is exactly wq#1479's shape (wq#1472 built
    this proof against a PR that only added files).
    """
    if not touched:
        return ""
    refs = [r for r in git(repo, "for-each-ref", "--format=%(refname)",
                           f"refs/remotes/{remote}/").splitlines() if r]
    candidates = [r for r in refs if r not in (exclude, f"refs/remotes/{remote}/HEAD")]
    for ref in candidates:
        if git_ok(repo, "merge-base", "--is-ancestor", local, ref):
            return ref
    for ref in candidates:
        if not git(repo, "diff", "--name-only", "--no-renames", local, ref, "--", *touched):
            return ref
    return ""


def inspect(repo, branch="main", remote="origin", fetch=True):
    remote_ref = f"{remote}/{branch}"
    if fetch:
        git(repo, "fetch", remote, check=False)
    behind, ahead = counts(repo, branch, remote_ref)
    local_tree = git(repo, "rev-parse", f"{branch}^{{tree}}")
    remote_tree = git(repo, "rev-parse", f"{remote_ref}^{{tree}}")
    base, touched, pr_ref = "", [], ""
    if ahead == 0:
        verdict = IN_SYNC if behind == 0 else BEHIND_ONLY
    elif local_tree == remote_tree:
        verdict = SAFE_RESET
    else:
        base = git(repo, "merge-base", branch, remote_ref)
        touched, carried = redundancy(repo, base, branch, remote_ref)
        if carried:
            verdict = REDUNDANT_AHEAD
        else:
            pr_ref = preserved_on(repo, branch, touched, remote,
                                  f"refs/remotes/{remote_ref}")
            verdict = PR_PENDING if pr_ref else CONTENT_DIVERGED
    return {
        "repo": repo, "branch": branch, "remote_ref": remote_ref,
        "ahead": ahead, "behind": behind, "preserved_on": pr_ref,
        "local_head": git(repo, "rev-parse", branch),
        "remote_head": git(repo, "rev-parse", remote_ref),
        "local_tree": local_tree, "remote_tree": remote_tree,
        "base": base, "touched": touched,
        "verdict": verdict,
    }


def explain(st):
    v = st["verdict"]
    if v == IN_SYNC:
        return f"{st['branch']} == {st['remote_ref']} at {st['local_head'][:7]}. Nothing to do."
    if v == BEHIND_ONLY:
        return (f"{st['remote_ref']} is {st['behind']} ahead of local {st['branch']}. A "
                f"fast-forward REWRITES FILES in this tree — deploy the remote content "
                f"first, then `git merge --ff-only {st['remote_ref']}`. Not this tool's job. "
                f"ahead=0 makes the ff itself always safe (no local commits at risk), but a "
                f"live tree can carry untracked files that collide with incoming paths — "
                f"wq#1279 hit exactly this on /home/lumen/bin. Set them aside first with "
                f"`git stash push -u -m <unique-tag>` (never bare `stash`/`stash pop` — the "
                f"stash stack is shared), do the fetch + ff-only merge, then `stash apply "
                f"<sha>` + `stash drop` to restore them byte-for-byte.")
    if v == SAFE_RESET:
        return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} but the trees "
                f"are identical ({st['local_tree'][:7]}) — the remote already carries this "
                f"content, squashed. `reconcile --apply` archives the local SHAs and moves "
                f"the ref; zero files change.")
    if v == REDUNDANT_AHEAD:
        return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} and "
                f"{st['behind']} behind, so the trees can never match — but the remote "
                f"already carries this content at every path those commits touch "
                f"({', '.join(st['touched'])}), so they are duplicates of landed work. "
                f"`reconcile --apply` archives them and rewinds the ref to the merge-base "
                f"{st['base'][:7]}; zero files change and the repo lands on BEHIND_ONLY, "
                f"i.e. fast-forwardable again.")
    if v == PR_PENDING:
        return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} with content "
                f"{st['remote_ref']} does not carry — but {st['preserved_on']} does, at "
                f"every path those commits touch ({', '.join(st['touched'])}). The work is "
                f"pushed and in flight as a PR, so rewinding loses nothing. `reconcile "
                f"--apply` archives the local SHAs and rewinds the ref to the merge-base "
                f"{st['base'][:7]}, leaving BEHIND_ONLY — which is what lets the PR land "
                f"(wq#771's guard refuses to merge over an unpushed commit). Unlike "
                f"SAFE_RESET/REDUNDANT_AHEAD this DOES change files: the tree gives up that "
                f"content until the PR merges and the fast-forward brings it back.")
    return (f"{st['branch']} is {st['ahead']} ahead of {st['remote_ref']} and the content "
            f"DIFFERS (local {st['local_tree'][:7]} vs remote {st['remote_tree'][:7]}). No "
            f"safe ref move exists: branch off {st['branch']}, open a PR, squash it, and "
            f"reconcile afterwards — or get a ruleset bypass from Frank.")


def reconcile(repo, branch="main", remote="origin", fetch=True, apply=False, tag=None):
    st = inspect(repo, branch, remote, fetch)
    if st["verdict"] not in (SAFE_RESET, REDUNDANT_AHEAD, PR_PENDING) or not apply:
        return st
    if git(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise GitError(f"{repo} has {branch} un-checked-out; refusing to move a ref out from "
                       "under a different HEAD")
    if st["verdict"] == PR_PENDING:
        # PR_PENDING is the one verdict that restores files rather than only moving a ref,
        # so it is also the one that can overwrite an edit nobody committed. The proof
        # covers committed content only — an uncommitted edit to a touched path exists
        # nowhere but this tree, and no PR branch is holding it.
        held = git(repo, "status", "--porcelain", "--", *st["touched"])
        if held:
            raise GitError(
                f"{repo} has uncommitted changes at a path this reconcile would restore "
                f"({', '.join(held.split())}); the PR branch does not carry them. Commit "
                f"or set them aside first.")
    if st["verdict"] == SAFE_RESET:
        # The whole tree already matches, so the remote ref itself is the safe landing spot.
        target, expected = st["remote_ref"], IN_SYNC
        proof = (f"content identical to {st['remote_ref']} {st['remote_head'][:7]} "
                 f"(tree {st['local_tree']})")
    elif st["verdict"] == REDUNDANT_AHEAD:
        # The remote has moved on, so only the merge-base is reachable without deploying.
        # Landing there is the point: it converts a divergence into a plain fast-forward.
        target, expected = st["base"], BEHIND_ONLY
        proof = (f"duplicates of landed work — {st['remote_ref']} {st['remote_head'][:7]} "
                 f"already carries this content at {', '.join(st['touched'])}")
    else:
        # Same landing spot, weaker guarantee: the content is pushed but not merged, so
        # this rewind DOES remove it from the tree until the PR lands. The archive tag and
        # the PR branch are both holding it.
        target, expected = st["base"], BEHIND_ONLY
        proof = (f"pushed but unlanded — {st['preserved_on']} already carries this content "
                 f"at {', '.join(st['touched'])}")
    tag = tag or f"archive/reconcile-{branch}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    git(repo, "tag", "-a", tag, st["local_head"], "-m",
        f"pre-reconcile snapshot of {branch} ({st['ahead']} unpushed commits); {proof}")
    # --soft, never --hard: index and working tree are left untouched, so uncommitted edits
    # and untracked files in a live deployment tree survive the reconcile intact.
    git(repo, "reset", "--soft", target)
    if st["verdict"] == PR_PENDING:
        # A soft reset alone would leave the dropped content STAGED, which is the same
        # blocker wearing different clothes: wq#771's land guard reads uncommitted paths
        # and unpushed paths from the same list, so the PR still could not merge. Restore
        # the touched paths — and only those — so the tree matches the ref it now sits on.
        # Untracked files and uncommitted work at every other path are still untouched.
        #
        # `git restore`, not `git checkout`: a path the ahead commits ADDED does not exist at
        # the merge-base, and `git checkout <base> -- <paths>` fails the WHOLE pathspec on the
        # first one it cannot find — leaving every touched path staged and the land still
        # blocked. `restore --source` deletes those instead of refusing, which is the correct
        # answer: the archive tag and the PR branch are both holding them.
        git(repo, "restore", f"--source={target}", "--staged", "--worktree", "--",
            *st["touched"])
    after = inspect(repo, branch, remote, fetch=False)
    if after["verdict"] != expected:
        raise GitError(f"reset left {repo} at {after['verdict']} — expected {expected}")
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
