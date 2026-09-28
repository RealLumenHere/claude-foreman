#!/usr/bin/env python3
"""wq#2934 verify-pass probe (of wq#2909's PR #1239, `fix(prune): add grace
period before pruning worktrees`).

Claim under test (from prune_worktrees.py's own docstring and README): "a
same-day merge gets one week to be reverted before its worktree is gone for
good."

`merge_age_days()` measured age from the branch tip's OWN commit timestamp,
not from when the branch actually became an ancestor of origin/main. This
probe builds a branch whose last commit is dated 10 days in the past (a
ticket that did its work, then sat blocked/on-hold for a while) and merges
it into origin/main just now (today) -- i.e. a merge that, by the fix's own
stated goal, should get a fresh 7-day revert window starting at merge time.

wq#2942 fixed it: `landing_age_days()` now reads the date of the commit that
brought the branch onto origin/main (or the wq ticket's `done_at`), so this
probe went from exit 1 to exit 0. It is kept as the regression check -- it
still fails against the tip-commit clock it was written to catch.

Run: python3 reports/wq2934_probe_grace_period_commit_vs_merge_age.py
Exit 0 = grace period held (claim upheld). Exit 1 = pruned immediately on a
just-merged branch (claim contradicted).
"""
import os
import subprocess
import sys
import tempfile
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ops", "wq2620-prune-stale-worktrees"))
import prune_worktrees as pw  # noqa: E402


def git(*args, cwd, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="wq2934-probe-")
    bare = os.path.join(tmp, "origin.git")
    work = os.path.join(tmp, "work")
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", bare], check=True)
    subprocess.run(["git", "clone", "-q", bare, work], check=True)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "T", cwd=work)
    with open(os.path.join(work, "f.txt"), "w") as f:
        f.write("1\n")
    git("add", "f.txt", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)

    root = os.path.join(tmp, "worktrees")
    os.makedirs(root, exist_ok=True)
    wt = os.path.join(root, "wq9099-a1")
    branch = "feat/wq9099-a1-blocked-10d"
    git("worktree", "add", "-q", "-b", branch, wt, "main", cwd=work)

    old_ts = int(time.time()) - 10 * 86400  # last commit made 10 days ago
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = str(old_ts)
    env["GIT_COMMITTER_DATE"] = str(old_ts)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("blocked ticket work\n")
    git("add", "g.txt", cwd=wt, env=env)
    git("commit", "-q", "-m", "ticket work (done 10d ago, then blocked)", cwd=wt, env=env)

    # The ticket un-blocks and lands NOW -- the merge into main happens at
    # current wall-clock time, well after the commit's own timestamp.
    git("checkout", "-q", "main", cwd=work)
    git("merge", "-q", "--no-ff", "-m", "merge blocked ticket", branch, cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)

    is_anc = subprocess.run(
        ["git", "-C", work, "merge-base", "--is-ancestor", branch, "origin/main"]
    ).returncode == 0
    tip_age = (pw._now_utc_naive() - pw.commit_ts(work, branch)).total_seconds() / 86400
    age, source = pw.landing_age_days(work, branch, "main", "wq9099-a1")
    print(f"branch merged into origin/main just now: {is_anc}")
    print(f"tip-commit clock (wq#2909, the bug): {tip_age:.2f} days")
    print(f"landing clock (wq#2942, the fix):    {age:.2f} days, per {source}")

    [r] = pw.run([root], apply=False, fetch=False)  # default grace_days=7
    print(f"classify() action={r['action']!r} reason={r['reason']!r}")
    if r["action"] == "prune":
        print("CONTRADICTED: worktree merged moments ago is immediately prunable "
              "-- the grace period gave ZERO protection.")
        return 1
    print("UPHELD: grace period held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
