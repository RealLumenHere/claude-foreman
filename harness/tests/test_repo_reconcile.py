"""harness/repo_reconcile.py — wq#686. Zero-quota: real git repos in tmp_path, a real
bare "origin" on disk, no network and no claude involved.

Two fixtures, one per proof the tool can offer:

  make_repos    the wq#686 shape — local main N ahead / 1 behind a remote whose single
                squashed commit already carries the same content, so the whole trees match.
  make_wq975    the wq#975 shape — the remote has ALSO landed later commits, so whole-tree
                equality is gone forever and only the per-touched-path proof survives.

Both come in a content-diverged variant, because the whole value of the tool is refusing
to reset when that content equality does NOT hold.
"""
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECONCILE = os.path.join(HARNESS_DIR, "repo_reconcile.py")


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {args}: {p.stdout}{p.stderr}"
    return p.stdout.strip()


def write(repo, name, text):
    with open(os.path.join(repo, name), "w") as fh:
        fh.write(text)


def commit(repo, name, text, msg):
    write(repo, name, text)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg)


def make_repos(tmp_path, local_final="final"):
    """origin/main = base + ONE squashed commit; local main = base + TWO commits.

    With local_final == "final" the two histories land on identical trees (the wq#686
    case); anything else makes the content genuinely diverge.
    """
    origin = str(tmp_path / "origin.git")
    work = str(tmp_path / "work")
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin], check=True,
                   capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")

    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")

    # The squashed PR commit that landed on origin while local main went its own way.
    git(work, "checkout", "-b", "squash")
    commit(work, "file.txt", "final\n", "feat: squashed PR")
    git(work, "push", "origin", "squash:main")

    git(work, "checkout", "main")
    commit(work, "file.txt", "mid\n", "direct-to-main 1")
    commit(work, "file.txt", f"{local_final}\n", "direct-to-main 2")
    git(work, "fetch", "origin")
    return origin, work


def make_wq975(tmp_path, local_final="final"):
    """origin/main = base + the squashed fix + two LATER commits; local main = base + a
    hand-committed duplicate of that same fix.

    The later commits touch a path (other.txt) the duplicate never touched — which is
    exactly what whole-tree equality mistook for real divergence in wq#975. Returns the
    merge-base too, since that is where a reconcile must land.
    """
    origin = str(tmp_path / "origin.git")
    work = str(tmp_path / "work")
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin], check=True,
                   capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")

    commit(work, "file.txt", "base\n", "base")
    commit(work, "other.txt", "o1\n", "other base")
    git(work, "push", "origin", "main")
    base = git(work, "rev-parse", "main")

    git(work, "checkout", "-b", "landed")
    commit(work, "file.txt", "final\n", "feat: squashed PR")
    commit(work, "other.txt", "o2\n", "later work the duplicate never saw")
    commit(work, "other.txt", "o3\n", "more of it")
    git(work, "push", "origin", "landed:main")

    git(work, "checkout", "main")
    commit(work, "file.txt", f"{local_final}\n", "direct-to-main duplicate")
    git(work, "fetch", "origin")
    return origin, work, base


def make_wq1472(tmp_path):
    """The wq#1472 deadlock: local main carries work origin/main lacks, and that same work
    is already pushed on a PR branch — whose rebase onto the remote tip gave it a DIFFERENT
    SHA than the local commit. Nothing is at risk of being lost; only `main` has not caught
    up. `--contains` answers "no" here, which is why the proof compares content.
    """
    origin, work, base = make_wq975(tmp_path, local_final="work-only-the-local-has")
    git(work, "checkout", "-b", "pr", "origin/main")
    commit(work, "file.txt", "work-only-the-local-has\n", "feat: the PR, rebased onto tip")
    git(work, "push", "origin", "pr")
    git(work, "checkout", "main")
    git(work, "fetch", "origin")
    return origin, work, base


def make_wq1479(tmp_path):
    """The wq#1479 shape: the PR branch does not rebase the local commits, it BUILDS on
    them — and then edits one of the files they touched. The blob proof cannot fire (the
    PR's version is a superset, so it differs), but the branch contains the local commits
    whole, which preserves strictly more.
    """
    origin, work, base = make_wq975(tmp_path, local_final="work-only-the-local-has")
    git(work, "checkout", "-b", "pr", "main")
    commit(work, "file.txt", "work-only-the-local-has\nand the PR's own edit on top\n",
           "feat: build on the local commits and edit the same file")
    git(work, "push", "origin", "pr")
    git(work, "checkout", "main")
    git(work, "fetch", "origin")
    return origin, work, base


def make_wq1479_new_file(tmp_path):
    """The ahead commits ADD a file. `git checkout <merge-base> -- <paths>` cannot restore a
    path that does not exist at the merge-base, and fails the whole pathspec when one does
    not — so every touched path stays staged and the land stays blocked."""
    origin, work, base = make_wq975(tmp_path, local_final="work-only-the-local-has")
    commit(work, "brand_new.txt", "added by the unpushed work\n", "feat: a new file")
    git(work, "checkout", "-b", "pr", "main")
    git(work, "push", "origin", "pr")
    git(work, "checkout", "main")
    git(work, "fetch", "origin")
    return origin, work, base


def make_wq1923(tmp_path):
    """The wq#1923 shape: local main is ahead ONLY — origin/main never moved on — and that
    one direct-to-main hotfix is pushed whole on a PR branch. Every other PR_PENDING fixture
    has the remote ahead too, which is why nothing caught the ahead-only landing spot: here
    the merge-base IS origin/main, so the rewind ends IN_SYNC with no fast-forward left.
    """
    origin = str(tmp_path / "origin.git")
    work = str(tmp_path / "work")
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin], check=True,
                   capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")

    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")
    base = git(work, "rev-parse", "main")

    commit(work, "file.txt", "a hotfix committed straight into the live tree\n",
           "fix: direct-to-main hotfix")
    git(work, "push", "origin", "main:pr")
    git(work, "fetch", "origin")
    return origin, work, base


def make_split_across_refs(tmp_path):
    """Two pushed refs that between them account for every touched path, but neither of
    which carries both. A coincidence, not a pushed PR — must stay CONTENT_DIVERGED.
    """
    origin = str(tmp_path / "origin.git")
    work = str(tmp_path / "work")
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin], check=True,
                   capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")

    commit(work, "a.txt", "a1\n", "base a")
    commit(work, "b.txt", "b1\n", "base b")
    git(work, "push", "origin", "main")

    git(work, "checkout", "-b", "landed")
    commit(work, "other.txt", "o2\n", "later work on the remote")
    git(work, "push", "origin", "landed:main")

    git(work, "checkout", "main")
    commit(work, "a.txt", "a2\n", "direct-to-main a")
    commit(work, "b.txt", "b2\n", "direct-to-main b")

    git(work, "checkout", "-b", "half-a", "origin/main")
    commit(work, "a.txt", "a2\n", "carries a only")
    git(work, "push", "origin", "half-a")

    git(work, "checkout", "-b", "half-b", "origin/main")
    commit(work, "b.txt", "b2\n", "carries b only")
    git(work, "push", "origin", "half-b")

    git(work, "checkout", "main")
    git(work, "fetch", "origin")
    return origin, work


def worktree_snapshot(repo):
    """Every tracked-or-not file's bytes, .git excluded — what "zero files change" means.

    Stronger than comparing `git status --porcelain`, which a --soft reset legitimately
    changes: it restages the dropped commits' content while touching no file on disk.
    """
    snap = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as fh:
                snap[os.path.relpath(path, repo)] = fh.read()
    return snap


def run(*args):
    p = subprocess.run([sys.executable, RECONCILE, *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_status_reports_safe_reset_when_trees_match(tmp_path):
    _, work = make_repos(tmp_path)
    assert git(work, "rev-parse", "main^{tree}") == git(work, "rev-parse", "origin/main^{tree}")
    rc, out = run("status", "--repo", work)  # exercises the real fetch path against the bare origin
    assert rc == 10, out
    assert "SAFE_RESET" in out and "ahead=2 behind=1" in out


def test_apply_moves_ref_and_leaves_the_tree_untouched(tmp_path):
    _, work = make_repos(tmp_path)
    old_head = git(work, "rev-parse", "main")
    # A live deployment tree always has in-flight edits; they must survive the reconcile.
    write(work, "file.txt", "final\nuncommitted hand-edit\n")
    write(work, "scratch.log", "untracked\n")
    before = git(work, "status", "--porcelain")

    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 0, out
    assert git(work, "rev-parse", "main") == git(work, "rev-parse", "origin/main")
    assert git(work, "status", "--porcelain") == before
    with open(os.path.join(work, "file.txt")) as fh:
        assert fh.read() == "final\nuncommitted hand-edit\n"

    tag = [ln.split()[-1] for ln in out.splitlines() if "as tag " in ln][0]
    assert git(work, "rev-parse", f"{tag}^{{commit}}") == old_head


def test_reconcile_is_idempotent(tmp_path):
    _, work = make_repos(tmp_path)
    assert run("reconcile", "--repo", work, "--no-fetch", "--apply")[0] == 0
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 0, out
    assert "IN_SYNC" in out and "as tag" not in out


def test_refuses_to_reset_when_content_actually_differs(tmp_path):
    _, work = make_repos(tmp_path, local_final="something-else")
    head = git(work, "rev-parse", "main")
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 20, out
    assert "CONTENT_DIVERGED" in out
    assert git(work, "rev-parse", "main") == head, "ref must not move on diverged content"
    assert git(work, "tag", "-l") == "", "no archive tag when nothing was reconciled"


def test_behind_only_is_reported_not_fixed(tmp_path):
    _, work = make_repos(tmp_path)
    git(work, "reset", "--hard", "HEAD~2")  # local back to base: purely behind
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out
    assert "BEHIND_ONLY" in out
    assert git(work, "rev-parse", "main") != git(work, "rev-parse", "origin/main")


def test_behind_only_explain_warns_about_untracked_file_collisions(tmp_path):
    """wq#1327: the manual ff-only recipe BEHIND_ONLY tells a worker to run can lose
    untracked files (wq#1279 hit this on /home/lumen/bin) unless they're stashed
    aside first. Locks in that the fix landed in explain(), not just in a commit
    message."""
    _, work = make_repos(tmp_path)
    git(work, "reset", "--hard", "HEAD~2")
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out
    assert "wq#1279" in out
    assert "stash" in out


def test_status_reports_redundant_ahead_once_the_remote_moved_on(tmp_path):
    _, work, _ = make_wq975(tmp_path)
    assert git(work, "rev-parse", "main^{tree}") != git(work, "rev-parse", "origin/main^{tree}"), \
        "wq#975: whole-tree equality is unreachable here — that is the bug being fixed"
    rc, out = run("status", "--repo", work, "--no-fetch")
    assert rc == 11, out
    assert "REDUNDANT_AHEAD" in out and "ahead=1 behind=3" in out
    assert "file.txt" in out, "the verdict must name the paths it proved"
    assert "other.txt" not in out, "paths only the remote touched are not the local's problem"


def test_apply_rewinds_to_the_merge_base_and_restores_the_fast_forward(tmp_path):
    _, work, base = make_wq975(tmp_path)
    old_head = git(work, "rev-parse", "main")
    write(work, "file.txt", "final\nuncommitted hand-edit\n")
    write(work, "scratch.log", "untracked\n")
    before = worktree_snapshot(work)

    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out  # BEHIND_ONLY: reconciled, and the deploy is still owed
    assert git(work, "rev-parse", "main") == base
    assert worktree_snapshot(work) == before, "a reconcile deploys nothing"

    tag = [ln.split()[-1] for ln in out.splitlines() if "as tag " in ln][0]
    assert git(work, "rev-parse", f"{tag}^{{commit}}") == old_head

    # The point of the whole exercise: wq#958 could not do this line.
    git(work, "checkout", "--", "file.txt")
    git(work, "merge", "--ff-only", "origin/main")
    assert git(work, "rev-parse", "main") == git(work, "rev-parse", "origin/main")


def test_redundant_reconcile_is_idempotent(tmp_path):
    _, work, base = make_wq975(tmp_path)
    assert run("reconcile", "--repo", work, "--no-fetch", "--apply")[0] == 30
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out
    assert "BEHIND_ONLY" in out and "as tag" not in out, "no second archive tag on a re-run"
    assert git(work, "rev-parse", "main") == base


def test_refuses_to_rewind_when_the_ahead_commit_carries_real_work(tmp_path):
    _, work, _ = make_wq975(tmp_path, local_final="work-only-the-local-has")
    head = git(work, "rev-parse", "main")
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 20, out
    assert "CONTENT_DIVERGED" in out
    assert git(work, "rev-parse", "main") == head, "ref must not move on diverged content"
    assert git(work, "tag", "-l") == "", "no archive tag when nothing was reconciled"


def test_status_reports_pr_pending_when_the_work_is_pushed_on_a_pr_branch(tmp_path):
    _, work, _ = make_wq1472(tmp_path)
    ahead = git(work, "rev-list", "origin/main..main")
    assert git(work, "rev-parse", "origin/pr") not in ahead, \
        "the rebase gave the PR a different SHA — a --contains proof would miss it"
    rc, out = run("status", "--repo", work, "--no-fetch")
    assert rc == 12, out
    assert "PR_PENDING" in out and "ahead=1 behind=3" in out
    assert "refs/remotes/origin/pr" in out, "the verdict must name the ref it proved against"


def test_pr_pending_accepts_a_branch_that_contains_the_local_commits_and_edits_them(tmp_path):
    """wq#1479: a PR that MODIFIES a path its own base commits touched can never satisfy
    the blob proof — its version is a superset. Containing the commits outright preserves
    strictly more than matching their blobs, so it qualifies on stronger grounds."""
    _, work, _ = make_wq1479(tmp_path)
    assert git(work, "diff", "--name-only", "main", "origin/pr", "--", "file.txt"), \
        "the fixture is only interesting while the PR's version DIFFERS at a touched path"
    rc, out = run("status", "--repo", work, "--no-fetch")
    assert rc == 12, out
    assert "PR_PENDING" in out and "refs/remotes/origin/pr" in out


def test_a_ref_that_merely_shares_history_is_not_a_preservation_proof(tmp_path):
    """The ancestor test runs in the right direction: local must be an ancestor of the ref,
    not the other way round. A ref the local branch has moved PAST proves nothing."""
    _, work, _ = make_wq975(tmp_path, local_final="work-only-the-local-has")
    git(work, "branch", "stale", "main~1")        # shares history, stops short of the work
    git(work, "push", "origin", "stale")
    git(work, "fetch", "origin")
    rc, out = run("status", "--repo", work, "--no-fetch")
    assert rc == 20, out
    assert "CONTENT_DIVERGED" in out


def test_pr_pending_apply_removes_paths_the_merge_base_never_had(tmp_path):
    """A staged leftover is the same blocker in different clothes — wq#771's land guard reads
    uncommitted and unpushed paths from one list, so a half-finished restore blocks the very
    land this verdict exists to unblock."""
    _, work, base = make_wq1479_new_file(tmp_path)
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out
    assert git(work, "rev-parse", "main") == base
    assert git(work, "status", "--porcelain") == "", "nothing may be left staged"
    assert not os.path.exists(os.path.join(work, "brand_new.txt"))
    # It is dropped, not lost: the archive tag and the PR branch both still carry it.
    assert git(work, "cat-file", "-e", "origin/pr:brand_new.txt") == ""

    git(work, "merge", "--ff-only", "origin/main")
    assert git(work, "rev-parse", "main") == git(work, "rev-parse", "origin/main")


def test_pr_pending_apply_rewinds_and_leaves_the_fast_forward_runnable(tmp_path):
    _, work, base = make_wq1472(tmp_path)
    old_head = git(work, "rev-parse", "main")
    write(work, "scratch.log", "untracked\n")

    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 30, out  # BEHIND_ONLY: rewound, deploy still owed
    assert git(work, "rev-parse", "main") == base

    tag = [ln.split()[-1] for ln in out.splitlines() if "as tag " in ln][0]
    assert git(work, "rev-parse", f"{tag}^{{commit}}") == old_head, "old head stays reachable"
    with open(os.path.join(work, "scratch.log")) as fh:
        assert fh.read() == "untracked\n", "untracked files are still none of its business"

    # The whole point: unlike REDUNDANT_AHEAD this restores the touched path itself, so the
    # ff needs no hand-cleanup first — and neither does the land guard that reads the same
    # dirty-path list. A --soft reset alone would leave file.txt staged and still blocking.
    assert git(work, "status", "--porcelain") == "?? scratch.log"
    git(work, "merge", "--ff-only", "origin/main")
    assert git(work, "rev-parse", "main") == git(work, "rev-parse", "origin/main")


def test_pr_pending_apply_lands_in_sync_when_the_branch_is_ahead_only(tmp_path):
    """wq#1923: with behind == 0 the merge-base IS the remote head, so the rewind lands
    IN_SYNC. The expectation was pinned to BEHIND_ONLY unconditionally, so the tool raised on
    its own successful reconcile — AFTER moving the ref, telling the caller a reconcile that
    had in fact worked had failed.
    """
    _, work, base = make_wq1923(tmp_path)
    old_head = git(work, "rev-parse", "main")
    rc, out = run("status", "--repo", work, "--no-fetch")
    assert rc == 12, out
    assert "ahead=1 behind=0" in out
    assert "leaving IN_SYNC" in out, "the advice must name the landing spot it actually hits"

    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 0, out  # IN_SYNC: reconciled, and no fast-forward is owed
    assert git(work, "rev-parse", "main") == base
    assert git(work, "status", "--porcelain") == "", "nothing may be left staged"

    tag = [ln.split()[-1] for ln in out.splitlines() if "as tag " in ln][0]
    assert git(work, "rev-parse", f"{tag}^{{commit}}") == old_head, "old head stays reachable"
    assert git(work, "cat-file", "-e", "origin/pr:file.txt") == "", "the PR branch still has it"


def test_pr_pending_refuses_when_a_touched_path_has_uncommitted_work(tmp_path):
    _, work, _ = make_wq1472(tmp_path)
    head = git(work, "rev-parse", "main")
    write(work, "file.txt", "a hand-edit no PR branch is carrying\n")

    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 2, out
    assert "uncommitted changes" in out and "file.txt" in out
    assert git(work, "rev-parse", "main") == head, "ref must not move"
    assert git(work, "tag", "-l") == "", "no archive tag when nothing was reconciled"
    with open(os.path.join(work, "file.txt")) as fh:
        assert fh.read() == "a hand-edit no PR branch is carrying\n", "the edit survives"


def test_two_refs_splitting_the_touched_paths_are_not_proof(tmp_path):
    _, work = make_split_across_refs(tmp_path)
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 20, out
    assert "CONTENT_DIVERGED" in out, "one ref must carry every touched path, or it proves nothing"
    assert git(work, "tag", "-l") == ""


def test_refuses_when_branch_is_not_checked_out(tmp_path):
    _, work = make_repos(tmp_path)
    git(work, "checkout", "-b", "elsewhere")
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 2, out
    assert "un-checked-out" in out
