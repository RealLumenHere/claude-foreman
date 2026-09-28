"""wq#1331 (WORKER_EXEC_CAPABILITY_GAP.md §3.2/§5.2): spawn-time preflight.

assert_worktree_repo (origin + git-common-dir vs the router-resolved repo),
assert_spawn_paths (needs:dir existence, /home/lumen/ path literals cited in detail
must be inside worktree ∪ add_dirs), the FOREMAN_NEEDS_DISABLE log-only rollback,
and the §5.2 corrupted-origin integration: LAUNCH_FAILURE WORKTREE_REPO_MISMATCH,
ticket left open, NO attempt-N.json (attempt not charged).
"""
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
import failure_record  # noqa: E402

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
WQ_BIN = "/home/lumen/bin/wq"
FAKE_CLAUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_claude.py")
FAKE_GOVERNOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_governor.py")


def make_repo(path, origin_url=None):
    os.makedirs(path)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@lumen"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("scratch\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    if origin_url:
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=path, check=True)
    return str(path)


# ---- assert_worktree_repo -------------------------------------------------------------

def test_true_worktree_of_expected_repo_passes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo", "http://git.lumen/a.git")
    wt = str(tmp_path / "wt")
    subprocess.run(["git", "-C", repo, "worktree", "add", wt, "-b", "t/x", "HEAD"],
                   capture_output=True, check=True)
    fworker.assert_worktree_repo(wt, repo, 999)  # must not raise


def test_foreign_checkout_posing_as_worktree_refused(tmp_path):
    # §5.2: "corrupt origin in a scratch worktree" — a checkout of another repo at the
    # worktree path: different origin AND different git-common-dir.
    repo = make_repo(tmp_path / "repo", "http://git.lumen/a.git")
    other = make_repo(tmp_path / "other", "http://git.lumen/b.git")
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.assert_worktree_repo(other, repo, 999)
    assert e.value.code == "WORKTREE_REPO_MISMATCH"
    assert repo in e.value.summary


def test_common_dir_alone_catches_mismatch_when_origins_equal(tmp_path):
    # two clones of the "same" origin are still not the same .git — a standalone clone
    # at the worktree path is not a worktree of the primary and must be refused.
    repo = make_repo(tmp_path / "repo", "http://git.lumen/a.git")
    clone = make_repo(tmp_path / "clone", "http://git.lumen/a.git")
    with pytest.raises(fworker.SpawnRefused):
        fworker.assert_worktree_repo(clone, repo, 999)


def test_disable_env_makes_worktree_assert_log_only(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo", "http://git.lumen/a.git")
    other = make_repo(tmp_path / "other", "http://git.lumen/b.git")
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    fworker.assert_worktree_repo(other, repo, 999)  # log-only, no raise


# ---- assert_spawn_paths ---------------------------------------------------------------

def test_cited_path_inside_roots_passes(tmp_path):
    fworker.assert_spawn_paths(
        999, "fix /home/lumen/foreman/harness/fworker.py and /home/lumen/kb/ISSUE-LOG.md.",
        str(tmp_path), "/home/lumen/foreman",
        ["/home/lumen/kb"], [])


def test_cited_path_outside_roots_refused(tmp_path, monkeypatch):
    # the exact wq#1152 signature: the ticket cites a repo the sandbox cannot reach.
    # wq#2184: held only with the advisory lever off; with it on the mention dispatches.
    monkeypatch.setenv("FOREMAN_CAPABILITY_ADVISORY", "off")
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.assert_spawn_paths(
            999, "flash it with /home/lumen/nle-flasher/flash.sh",
            str(tmp_path), "/home/lumen/foreman", ["/home/lumen/kb"], [])
    assert e.value.code == "CITED_PATH_UNREACHABLE"
    assert "/home/lumen/nle-flasher" in e.value.summary


def test_needs_dir_makes_cited_path_reachable(tmp_path):
    d = tmp_path / "nle-flasher"
    d.mkdir()
    fworker.assert_spawn_paths(
        999, f"flash it with {d}/flash.sh — also read /home/lumen/kb/ISSUE-LOG.md",
        str(tmp_path / "wt"), "/home/lumen/foreman",
        ["/home/lumen/kb", str(d)], [str(d)])


def test_missing_needs_dir_refused(tmp_path):
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.assert_spawn_paths(999, "", str(tmp_path), "/home/lumen/foreman",
                                   [], [str(tmp_path / "does-not-exist")])
    assert "not an existing directory" in e.value.summary


def test_disable_env_makes_path_assert_log_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    fworker.assert_spawn_paths(999, "see /home/lumen/nowhere/at-all",
                               str(tmp_path), "/home/lumen/foreman", [], [])


def test_cited_paths_extraction_strips_sentence_punctuation():
    got = fworker.cited_paths(
        "read /home/lumen/kb/ISSUE-LOG.md, then /home/lumen/foreman/harness/guard.sh; "
        "prefix-only /home/lumen/ mentions and bare /home/lumen do not count")
    assert got == ["/home/lumen/kb/ISSUE-LOG.md", "/home/lumen/foreman/harness/guard.sh"]


def test_prefix_sibling_dir_is_not_under_root():
    # /home/lumen/kb-evil must not ride on the /home/lumen/kb grant.
    assert not fworker._under("/home/lumen/kb-evil/x", "/home/lumen/kb")
    assert fworker._under("/home/lumen/kb/x", "/home/lumen/kb")
    assert fworker._under("/home/lumen/kb", "/home/lumen/kb")


# ---- add_dirs_for union (§3.2 "add_dirs_for() unions needs.dir") ----------------------

def test_add_dirs_for_unions_needs_dirs_without_duplicates():
    base = fworker.add_dirs_for("standard-engineering")
    got = fworker.add_dirs_for("standard-engineering", ["/x", base[0], "/x"])
    assert got[:len(base)] == list(base)
    assert got.count("/x") == 1 and got.count(base[0]) == 1


# ---- wq#1439 (BROKER_WIRING.md §2.3): add_dirs_for applies the lint's DIR_DENY --------

@pytest.mark.parametrize("denied", [
    "/home/lumen/.secrets",            # deny:prefix
    "/home/lumen/.ssh/keys",           # deny:prefix, nested
    "/home/lumen",                     # deny:path (exact)
    "/home/lumen/agents/work-queue/queue.db",  # deny:path_glob
    "/home/lumen/kb/service.token",    # deny:basename
])
def test_add_dirs_for_refuses_a_denied_needs_dir_at_spawn(denied):
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.add_dirs_for("standard-engineering", [denied])
    assert e.value.code == "CAPABILITY_DENIED_BY_POLICY"
    assert e.value.refused and denied in e.value.refused[0]
    assert denied in e.value.summary


def test_add_dirs_for_refuses_the_whole_list_not_the_safe_half():
    # §4 row vi: a denied dir never yields a partial grant of the safe remainder.
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.add_dirs_for("standard-engineering", ["/home/lumen/agent", "/home/lumen/.ssh"])
    assert e.value.refused == ["dir(/home/lumen/.ssh) deny:prefix (/home/lumen/.ssh)"]


def test_add_dirs_for_still_unions_an_allowed_needs_dir():
    got = fworker.add_dirs_for("standard-engineering", ["/home/lumen/agent"])
    assert got[-1] == "/home/lumen/agent"


def test_build_claude_cmd_carries_needs_dir_as_add_dir():
    # §4 row i, argv half: the widened list renders as a `--add-dir` pair the child sees.
    add_dirs = fworker.add_dirs_for("standard-engineering", ["/home/lumen/agent"])
    cmd = fworker.build_claude_cmd("p", "m", "high", "/tmp/s.json", add_dirs)
    i = cmd.index("/home/lumen/agent")
    assert cmd[i - 1] == "--add-dir"


# ---- §5.2 integration: corrupted origin -> LAUNCH_FAILURE, attempt NOT charged --------

def test_corrupted_origin_launch_failure_not_charged(tmp_path):
    repo = make_repo(tmp_path / "repo", "http://git.lumen/a.git")
    runs_root = str(tmp_path / "_runs")
    worktrees_root = str(tmp_path / "worktrees")
    env = dict(os.environ)
    env.update({
        "WQ_DB": str(tmp_path / "scratch_wq.db"),
        "WQ_AGENT": "test-spawn-refused-agent",
        "FOREMAN_EVENTS_DIR": str(tmp_path / "events"),
        "FOREMAN_RUNS_ROOT": runs_root,
        "FOREMAN_WORKTREES_ROOT": worktrees_root,
        "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent"),
        "FOREMAN_STATE_FILE": str(tmp_path / "state.json"),
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_NTFY_BIN": "/bin/true",
        "FOREMAN_NO_REFILL": "1",
    })
    os.makedirs(env["FOREMAN_EVENTS_DIR"], exist_ok=True)
    title = "scratch corrupted origin ticket"
    p = subprocess.run([WQ_BIN, "add", "--title", title],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    ticket_id = int(p.stdout.strip().split()[2])

    # plant a checkout of a DIFFERENT repo at the exact (ticket, attempt) worktree
    # path, on the exact branch name make_worktree computes — the wq#649 adopt path
    # picks it up, and the wq#1331 assertion must then refuse it before spawn.
    branch = f"chore/wq{ticket_id}-{fworker.slugify(title)}-a1"
    wt = os.path.join(worktrees_root, f"wq{ticket_id}-a1")
    other = make_repo(tmp_path / "other", "http://git.lumen/b.git")
    os.makedirs(worktrees_root, exist_ok=True)
    subprocess.run(["git", "clone", "-q", other, wt], capture_output=True, check=True)
    subprocess.run(["git", "-C", wt, "checkout", "-q", "-b", branch],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", wt, "remote", "set-url", "origin",
                    "http://git.lumen/b.git"], capture_output=True, check=True)

    r = subprocess.run(
        [sys.executable, FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
         "--model", "claude-sonnet-5", "--effort", "high",
         "--archetype", "mechanical-edit", "--claude-bin", FAKE_CLAUDE,
         "--worker-id", f"test-fworker-wq{ticket_id}"],
        capture_output=True, text=True, env=env, timeout=120)

    assert r.returncode == fworker.EXIT_LAUNCH_REFUSED, r.stdout + r.stderr
    assert "WORKTREE_REPO_MISMATCH" in r.stdout

    # ticket back to open — not blocked, not claimed.
    show = subprocess.run([WQ_BIN, "show", str(ticket_id), "--json"],
                          capture_output=True, text=True, env=env)
    row = json.loads(show.stdout)
    assert row["status"] == "open"

    # attempt NOT charged: no attempt-N.json, so next dispatch recomputes attempt 1...
    run_dir = os.path.join(runs_root, f"wq{ticket_id}")
    assert not [f for f in os.listdir(run_dir) if f.startswith("attempt-")]
    # ...but failure.json landed for slice-1 measurement, triaged HARNESS_DEFECT.
    with open(os.path.join(run_dir, "failure.json")) as f:
        rec = json.load(f)
    assert rec["classification"] == "LAUNCH_FAILURE"
    assert rec["triage"] == failure_record.TRIAGE_HARNESS_DEFECT
    assert "WORKTREE_REPO_MISMATCH" in rec["summary"]
