#!/usr/bin/env python3
"""wq#2960 verify-pass probe (of wq#2942's PR #1257, `fix(prune): key the
grace period to landing time, not commit date`).

Claim under test: landing_age_days() now protects EVERY merged worktree with
a fresh grace window from actual landing time, not the branch tip's own
(possibly much older) commit date -- wq#2934's bug, fixed for good.

wq#2942's own fix has two fallbacks when a merge is a fast-forward (so there
is no merge commit anywhere to date the landing): the wqNNNN-aN ticket's
`done_at`, or -- when neither a merge commit NOR a done_at exists -- nothing:
landing_age_days() falls back to the branch tip's own commit date, which is
exactly wq#2934's bug. `done_at` is only ever looked up for a fworker-shaped
name (`FWORKER_WT_RE = wq(\\d+)-a\\d+`). A `/home/lumen/scratch/worktrees`
worktree -- lumen-pr's own kind, the one wq2620's docstring opens with
("lumen-pr commit/land never removes the linked worktree it worked in") --
is never named that way, so it can never get a done_at fallback.

This probe builds exactly that: a scratch-style worktree whose one commit is
10 days old, fast-forwarded into origin/main just now (so origin/main's tip
IS the branch tip -- no merge commit, no commit dated after it either). The
existing test suite's own
`test_late_landing_protects_a_non_fworker_scratch_worktree` covers a
non-fworker scratch worktree but merges it with `--no-ff` (ff=False), so a
merge commit exists to date the landing -- it does not exercise the
fast-forward path. `test_fast_forward_landing_falls_back_to_wq_done_at`
exercises fast-forward but only for an fworker-named worktree. No test
combines "fast-forward" with "non-fworker name": that intersection is the gap.

Run: python3 probe_ff_scratch_gap.py
Exit 0 = grace period held (claim upheld). Exit 1 = pruned immediately on a
just-merged scratch worktree -- the grace period gave ZERO protection.
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
    tmp = tempfile.mkdtemp(prefix="wq2960-probe-")
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

    # Scratch-style root and a lumen-pr-shaped worktree name -- NOT wqNNNN-aN.
    root = os.path.join(tmp, "scratch", "worktrees")
    os.makedirs(root, exist_ok=True)
    wt_name = "myrepo-feat-wq9030-late"
    branch = "feat/wq9030-late"
    wt = os.path.join(root, wt_name)
    git("worktree", "add", "-q", "-b", branch, wt, "main", cwd=work)

    old_ts = int(time.time()) - 10 * 86400  # last commit made 10 days ago
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = str(old_ts)
    env["GIT_COMMITTER_DATE"] = str(old_ts)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("scratch ticket work\n")
    git("add", "g.txt", cwd=wt, env=env)
    git("commit", "-q", "-m", "scratch work (done 10d ago, then blocked)", cwd=wt, env=env)

    # Lands NOW, by FAST-FORWARD -- no merge commit, origin/main's tip becomes
    # the branch's own (10-day-old) commit. No done_at exists for this name.
    git("checkout", "-q", "main", cwd=work)
    git("merge", "-q", "--ff-only", branch, cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)

    # No .owners record either -- an unowned scratch worktree, same as an
    # abandoned lumen-pr session; classify() still must not zero out the grace
    # window just because no liveness AND no landing-date source exists.
    pw.WQ_DB = os.path.join(tmp, "no-such.db")

    is_anc = subprocess.run(
        ["git", "-C", work, "merge-base", "--is-ancestor", branch, "origin/main"]
    ).returncode == 0
    tip_age = (pw._now_utc_naive() - pw.commit_ts(work, branch)).total_seconds() / 86400
    age, source = pw.landing_age_days(work, branch, "main", wt_name)
    print(f"branch fast-forwarded into origin/main just now: {is_anc}")
    print(f"tip-commit clock (wq#2909/wq#2934, the bug): {tip_age:.2f} days")
    print(f"landing clock (wq#2942, the fix):            {age:.2f} days, per {source}")

    [r] = pw.run([root], apply=False, fetch=False)  # default grace_days=7
    print(f"classify() action={r['action']!r} reason={r['reason']!r}")
    if r["action"] == "prune":
        print("CONTRADICTED: a fast-forwarded, non-fworker scratch worktree merged "
              "moments ago is immediately prunable -- wq#2942 only patched the "
              "fworker done_at fallback, not the scratch-worktree fast-forward gap.")
        return 1
    print("UPHELD: grace period held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
