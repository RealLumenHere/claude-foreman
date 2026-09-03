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


def test_refuses_when_branch_is_not_checked_out(tmp_path):
    _, work = make_repos(tmp_path)
    git(work, "checkout", "-b", "elsewhere")
    rc, out = run("reconcile", "--repo", work, "--no-fetch", "--apply")
    assert rc == 2, out
    assert "un-checked-out" in out
