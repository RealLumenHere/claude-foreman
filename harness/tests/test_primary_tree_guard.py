"""primary_tree_guard tests — wq#900 adversarial verify.

Zero quota. Simulates the audited failure (a worker's raw `git checkout` left a
PRIMARY tree on a feature branch) against real throwaway git repos and asserts
detect + alert + restore-only-when-clean. The guard.sh deny half of wq#900 is
covered in test_guard.py.
"""
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import primary_tree_guard as ptg
import agents_spec


@pytest.fixture(autouse=True)
def _isolated_dirty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ptg, "DIRTY_STATE_PATH", str(tmp_path / "dirty.json"))
    monkeypatch.setattr(ptg, "STASH_STATE_PATH", str(tmp_path / "stash.json"))
    monkeypatch.setattr(ptg, "UNTRACKED_STATE_PATH", str(tmp_path / "untracked.json"))
    monkeypatch.setattr(ptg, "BEHIND_STATE_PATH", str(tmp_path / "behind.json"))
    monkeypatch.setattr(ptg, "AQ_BIN", str(tmp_path / "no-aq"))


def make_primary(tmp_path, name="primary"):
    repo = tmp_path / name
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "test@test")
    git("config", "user.name", "test")
    (repo / "f.txt").write_text("v1\n")
    git("add", "f.txt")
    git("commit", "-qm", "init")
    git("branch", "-M", "main")
    return repo, git


def test_clean_tree_reports_no_drift(tmp_path):
    repo, _ = make_primary(tmp_path)
    r = ptg.check_tree(str(repo))
    assert r["default"] == "main" and not r["drifted"] and not r["error"]


def test_worker_checkout_detected_restored_and_alerted(tmp_path, monkeypatch):
    # The audit case: clean primary left on a feature branch.
    repo, git = make_primary(tmp_path)
    git("checkout", "-qb", "research/wq633-849-headroom-judge")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))

    drifted = ptg.check_and_restore_all(repos=[str(repo)], context="test")
    assert len(drifted) == 1 and drifted[0]["restored"]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == "main"
    assert len(alerts) == 1 and "wq#900" in alerts[0][0]
    assert "auto-restored" in alerts[0][1] and "test" in alerts[0][1]


def test_dirty_tree_alerts_but_is_not_restored(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    git("checkout", "-qb", "feat/x")
    (repo / "f.txt").write_text("uncommitted work\n")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))

    drifted = ptg.check_and_restore_all(repos=[str(repo)])
    assert len(drifted) == 1 and not drifted[0]["restored"] and drifted[0]["dirty"]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == "feat/x"
    assert (repo / "f.txt").read_text() == "uncommitted work\n"
    assert len(alerts) == 1 and "NOT auto-restored" in alerts[0][1]


def test_untracked_files_do_not_block_restore(tmp_path, monkeypatch):
    # wq#634's PXPIPE_CANARY.md case: untracked droppings are not "dirty" for the
    # branch restore — the checkout back to main must still happen.
    repo, git = make_primary(tmp_path)
    git("checkout", "-qb", "feat/y")
    (repo / "PXPIPE_CANARY.md").write_text("dropped\n")
    monkeypatch.setattr(ptg, "alert", lambda t, m: None)

    drifted = ptg.check_and_restore_all(repos=[str(repo)])
    assert drifted[0]["restored"]
    assert (repo / "PXPIPE_CANARY.md").exists()


def test_missing_repo_is_reported_not_raised(tmp_path):
    r = ptg.check_tree(str(tmp_path / "nope"))
    assert r["error"] == "not a git repo" and not r["drifted"]


def test_primary_trees_env_override(monkeypatch):
    monkeypatch.setenv("FOREMAN_PRIMARY_TREES", "/a:/b::/a")
    assert ptg.primary_trees() == ["/a", "/b"]


def test_primary_trees_default_includes_registry(monkeypatch):
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/foreman" in trees and "/home/lumen/bin" in trees


def test_primary_trees_default_includes_kb_and_nest(monkeypatch):
    # wq#1752 (ISSUE-LOG #302): kb and the shallow nest archive are default
    # add_dirs (agents.yaml) but were reachable through neither STATIC_PRIMARY_TREES
    # nor router.LANE_REPO (kb had no entry; LANE_REPO["nest"] points one level
    # deeper, at .../nest/NoLongerEvil-Thermostat) — so a worker could Edit/Write
    # either tree despite fworker setting FOREMAN_PRIMARY_TREES from this function.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/kb" in trees and "/home/lumen/projects/nest" in trees, trees


def test_primary_trees_default_includes_work_queue(monkeypatch):
    # wq#1761 (ISSUE-LOG #313, wq#1760's verify-pass of wq#1752/PR#459): same bug
    # class as kb/nest above, third default add_dir. work-queue has no
    # router.LANE_REPO entry and is not a git repo (no .git — a shallow-nest-style
    # exception, not a crash: check_tree() already handles "not a git repo"
    # without raising), so STATIC_PRIMARY_TREES is the only thing that can ever
    # cover it.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/agents/work-queue" in trees, trees


def test_primary_trees_covers_every_agents_yaml_add_dir(monkeypatch):
    # wq#1761: re-audit ask — this bug class (a default add_dir granted by
    # agents.yaml but unreachable through primary_trees()) has now recurred twice
    # (kb+nest in wq#1752, work-queue here). Rather than hardcode a third
    # allowlist that itself can drift, assert directly against the live
    # agents.yaml defaults so a future add_dir is caught automatically instead of
    # needing its own wq ticket.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = set(ptg.primary_trees())
    add_dirs = agents_spec.load().default_add_dirs
    assert add_dirs, "agents.yaml defaults.add_dirs is empty — test fixture drifted"
    missing = [d for d in add_dirs if d not in trees]
    assert not missing, f"add_dirs missing from primary_trees(): {missing}"


def test_cli_status_exit_codes(tmp_path, monkeypatch, capsys):
    repo, git = make_primary(tmp_path)
    assert ptg.main(["status", "--repo", str(repo)]) == 0
    git("checkout", "-qb", "feat/z")
    assert ptg.main(["status", "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "DRIFTED" in out
    # status never mutates
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == "feat/z"


def test_cli_check_restores_and_dedups_alerts(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    monkeypatch.setattr(ptg, "STATE_PATH", str(tmp_path / "state.json"))
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append(m))

    # clean drift: restored, alerted, exit 0
    git("checkout", "-qb", "feat/a")
    assert ptg.main(["check", "--repo", str(repo)]) == 0
    assert len(alerts) == 1

    # dirty drift: alert once, second timer run stays quiet (no spam), exit 1 both
    git("checkout", "-qb", "feat/b")
    (repo / "f.txt").write_text("dirty\n")
    assert ptg.main(["check", "--repo", str(repo)]) == 1
    assert ptg.main(["check", "--repo", str(repo)]) == 1
    assert len(alerts) == 2

    # cleaned up by a human: state entry clears, next episode alerts again
    git("checkout", "-q", "--", "f.txt")
    git("checkout", "-q", "main")
    assert ptg.main(["check", "--repo", str(repo)]) == 0
    git("checkout", "-qb", "feat/c")
    (repo / "f.txt").write_text("dirty again\n")
    assert ptg.main(["check", "--repo", str(repo)]) == 1
    assert len(alerts) == 3


def test_primary_trees_default_includes_minerva(monkeypatch):
    # wq#1540: minerva has no router.LANE_REPO entry; only the static list reaches it.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    assert "/home/lumen/minerva" in ptg.primary_trees()


def test_primary_trees_default_includes_fleet_inventory(monkeypatch):
    # wq#2618: repo_sweep.EXTRA_REPOS (wq#1453-1456) already covered the fleet
    # inventory SoT repo for divergence sweeps; this guard's registry had not,
    # so a dirty/behind fleet-inventory tree paged nowhere.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    assert "/home/lumen/fleet/inventory" in ptg.primary_trees()


def test_dirty_under_24h_does_not_page(tmp_path, monkeypatch):
    repo, _ = make_primary(tmp_path)
    (repo / "f.txt").write_text("edit\n")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    assert ptg.check_dirty_age(repos=[str(repo)], state=state) == []
    assert alerts == [] and str(repo) in state


def test_dirty_over_24h_pages_once_then_repeats_daily(tmp_path, monkeypatch):
    repo, _ = make_primary(tmp_path)
    (repo / "f.txt").write_text("edit\n")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    t0 = os.path.getmtime(repo / "f.txt")

    stale = ptg.check_dirty_age(repos=[str(repo)], state=state, now=t0 + 25 * 3600)
    assert len(stale) == 1 and stale[0]["paged"] and stale[0]["files"] == 1
    assert len(alerts) == 1 and "wq#1540" in alerts[0][0] and "f.txt" in alerts[0][1]

    # next timer run an hour later: still stale, no second page
    stale = ptg.check_dirty_age(repos=[str(repo)], state=state, now=t0 + 26 * 3600)
    assert len(stale) == 1 and not stale[0]["paged"] and len(alerts) == 1

    # a day after the page: re-page
    ptg.check_dirty_age(repos=[str(repo)], state=state, now=t0 + 50 * 3600)
    assert len(alerts) == 2


def test_dirty_age_survives_file_rewrites(tmp_path, monkeypatch):
    # A daemon rewriting a tracked data file bumps its mtime; the first-seen time in
    # state must keep the age from resetting.
    repo, _ = make_primary(tmp_path)
    (repo / "f.txt").write_text("edit\n")
    monkeypatch.setattr(ptg, "alert", lambda t, m: None)
    t0 = os.path.getmtime(repo / "f.txt")
    state = {}
    ptg.check_dirty_age(repos=[str(repo)], state=state, now=t0)
    (repo / "f.txt").write_text("edit again\n")
    stale = ptg.check_dirty_age(repos=[str(repo)], state=state, now=t0 + 25 * 3600)
    assert len(stale) == 1


def test_dirty_age_dates_first_sighting_from_file_mtime(tmp_path, monkeypatch):
    # Empty state (fresh deploy, wiped /tmp) must not grant already-old edits
    # another 24h.
    repo, _ = make_primary(tmp_path)
    (repo / "f.txt").write_text("edit\n")
    old = os.path.getmtime(repo / "f.txt") - 3 * 86400
    os.utime(repo / "f.txt", (old, old))
    monkeypatch.setattr(ptg, "alert", lambda t, m: None)
    stale = ptg.check_dirty_age(repos=[str(repo)], state={})
    assert len(stale) == 1 and stale[0]["age_s"] >= 3 * 86400


def test_clean_tree_and_untracked_files_are_not_dirty(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    (repo / "dropping.txt").write_text("untracked\n")
    monkeypatch.setattr(ptg, "alert", lambda t, m: pytest.fail("paged"))
    state = {str(repo): {"since": 0, "paged": 0}}
    assert ptg.check_dirty_age(repos=[str(repo)], state=state, now=10 * 86400) == []
    assert state == {}, "clean tree must drop its episode"


def test_cli_check_pages_stale_dirty_and_exits_1(tmp_path, monkeypatch, capsys):
    repo, _ = make_primary(tmp_path)
    (repo / "f.txt").write_text("edit\n")
    old = os.path.getmtime(repo / "f.txt") - 2 * 86400
    os.utime(repo / "f.txt", (old, old))
    monkeypatch.setattr(ptg, "STATE_PATH", str(tmp_path / "state.json"))
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append(t))

    assert ptg.main(["check", "--repo", str(repo)]) == 1
    assert ptg.main(["check", "--repo", str(repo)]) == 1
    assert alerts == ["PRIMARY tree dirty >24h (wq#1540)"]
    assert "STALE" in capsys.readouterr().out
    assert ptg.main(["status", "--repo", str(repo)]) == 1


# ---- wq#2504: stale untracked-file stashes raise one aq row, never dropped ----

def stash_untracked(repo, git, name="swept.md"):
    (repo / name).write_text("untracked\n")
    git("stash", "push", "-u", "-m", f"sweep {name}")
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "stash@{0}"],
                          capture_output=True, text=True).stdout.strip()


def test_stale_untracked_stash_alerts_once_and_is_kept(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    sha = stash_untracked(repo, git)
    rows = []
    monkeypatch.setattr(ptg, "aq_alert", lambda k, t, b: rows.append((k, t, b)))
    state = {}
    now = time.time() + 25 * 3600

    stale = ptg.check_stale_stashes(repos=[str(repo)], state=state, now=now)
    assert len(stale) == 1 and stale[0]["alerted"] and stale[0]["files"] == 1
    assert len(rows) == 1 and rows[0][0] == f"stale-stash-{sha[:12]}"
    assert "wq#2504" in rows[0][1] and "nothing was dropped" in rows[0][2]

    # next timer run: still reported, no second row
    stale = ptg.check_stale_stashes(repos=[str(repo)], state=state, now=now + 3600)
    assert len(stale) == 1 and not stale[0]["alerted"] and len(rows) == 1

    # report only — the stash is still there
    listed = subprocess.run(["git", "-C", str(repo), "stash", "list", "--format=%H"],
                            capture_output=True, text=True).stdout.split()
    assert listed == [sha]


def test_fresh_or_tracked_only_stash_does_not_alert(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    monkeypatch.setattr(ptg, "aq_alert", lambda k, t, b: pytest.fail("alerted"))
    (repo / "f.txt").write_text("tracked edit\n")
    git("stash", "push", "-m", "tracked only")
    assert ptg.check_stale_stashes(repos=[str(repo)], state={},
                                   now=time.time() + 5 * 86400) == []
    stash_untracked(repo, git)
    assert ptg.check_stale_stashes(repos=[str(repo)], state={}) == []  # < 24h


def test_dropped_stash_leaves_state(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    sha = stash_untracked(repo, git)
    monkeypatch.setattr(ptg, "aq_alert", lambda k, t, b: True)
    later = time.time() + 2 * 86400
    state = {}
    ptg.check_stale_stashes(repos=[str(repo)], state=state, now=later)
    assert sha in state
    git("stash", "drop", "-q")
    ptg.check_stale_stashes(repos=[str(repo)], state=state, now=later)
    assert state == {}


def test_cli_stale_stash_reports_without_failing_run(tmp_path, monkeypatch, capsys):
    # A stale stash may be a deliberate backup (wq#2477), so it must not fail the timer.
    repo, git = make_primary(tmp_path)
    stash_untracked(repo, git)
    monkeypatch.setattr(ptg, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(ptg, "STASH_ALERT_AFTER_S", 0)
    rows = []
    monkeypatch.setattr(ptg, "aq_alert", lambda k, t, b: rows.append(k))
    assert ptg.main(["check", "--repo", str(repo)]) == 0
    assert ptg.main(["check", "--repo", str(repo)]) == 0
    assert len(rows) == 1
    assert "STASH" in capsys.readouterr().out


# ---- wq#2618: untracked deploy artifacts >24h page too (git status --porcelain's
# other half — check_dirty_age deliberately skips untracked, see its docstring) ----

def test_untracked_dropping_under_24h_does_not_page(tmp_path, monkeypatch):
    repo, _ = make_primary(tmp_path)
    (repo / "dropped.py").write_text("new file\n")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    assert ptg.check_untracked_age(repos=[str(repo)], state=state) == []
    assert alerts == [] and str(repo) in state


def test_untracked_dropping_over_24h_pages_once_then_repeats_daily(tmp_path, monkeypatch):
    repo, _ = make_primary(tmp_path)
    (repo / "drift_plan.py").write_text("deploy artifact\n")
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    t0 = os.path.getmtime(repo / "drift_plan.py")

    stale = ptg.check_untracked_age(repos=[str(repo)], state=state, now=t0 + 25 * 3600)
    assert len(stale) == 1 and stale[0]["paged"] and stale[0]["files"] == 1
    assert len(alerts) == 1 and "wq#2618" in alerts[0][0]
    assert "drift_plan.py" in alerts[0][1]

    stale = ptg.check_untracked_age(repos=[str(repo)], state=state, now=t0 + 26 * 3600)
    assert len(stale) == 1 and not stale[0]["paged"] and len(alerts) == 1

    ptg.check_untracked_age(repos=[str(repo)], state=state, now=t0 + 50 * 3600)
    assert len(alerts) == 2


def test_gitignored_untracked_dropping_never_pages(tmp_path, monkeypatch):
    # The prescribed fix (add it to .gitignore, minerva 4854d86-style) must clear
    # the condition, not just rename it.
    repo, git = make_primary(tmp_path)
    (repo / ".gitignore").write_text("drift_plan.py\n")
    git("add", ".gitignore")
    git("commit", "-qm", "ignore drift_plan.py")
    (repo / "drift_plan.py").write_text("deploy artifact\n")
    monkeypatch.setattr(ptg, "alert", lambda t, m: pytest.fail("paged"))
    assert ptg.check_untracked_age(repos=[str(repo)], state={},
                                   now=time.time() + 5 * 86400) == []


def test_committing_the_dropping_clears_the_episode(tmp_path, monkeypatch):
    repo, git = make_primary(tmp_path)
    (repo / "drift_plan.py").write_text("deploy artifact\n")
    monkeypatch.setattr(ptg, "alert", lambda t, m: None)
    state = {str(repo): {"since": 0, "paged": 0}}
    git("add", "drift_plan.py")
    git("commit", "-qm", "land it")
    assert ptg.check_untracked_age(repos=[str(repo)], state=state,
                                   now=10 * 86400) == []
    assert state == {}


# ---- wq#2618: a primary sitting behind origin/<default> >24h pages — the exact
# wq#2600 shape (landed PRs never fast-forwarded into the tree the fleet runs) ----

def make_primary_with_origin(tmp_path, name="primary"):
    """A primary clone plus a bare 'origin' remote, so behind_count() has a real
    refs/remotes/origin/<default> to compare against."""
    bare = tmp_path / f"{name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo, git = make_primary(tmp_path, name=name)
    git("remote", "add", "origin", str(bare))
    git("push", "-q", "origin", "main")
    git("branch", "-q", "--set-upstream-to=origin/main", "main")
    # The bare repo's own HEAD symref defaults to init.defaultBranch (often
    # "master"), which push never touches — leave it pointing at a ref that was
    # never pushed and a later `git clone` of the bare repo fails to check
    # anything out ("remote HEAD refers to nonexistent ref").
    subprocess.run(["git", "-C", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True)
    return repo, git, bare


def advance_origin(bare, repo, git, tmp_path):
    """Push one more commit from a throwaway clone so origin/main moves ahead of
    `repo`'s HEAD without touching repo's own working tree."""
    clone_dir = tmp_path / "origin-advancer"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "config", "user.email", "test@test"],
                   check=True)
    subprocess.run(["git", "-C", str(clone_dir), "config", "user.name", "test"],
                   check=True)
    (clone_dir / "g.txt").write_text("v2\n")
    subprocess.run(["git", "-C", str(clone_dir), "add", "g.txt"], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "commit", "-qm", "advance"], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "push", "-q", "origin", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)


def test_behind_count_none_when_no_origin_ref(tmp_path):
    repo, _ = make_primary(tmp_path)
    assert ptg.behind_count(str(repo), "main") is None


def test_behind_count_zero_when_in_sync(tmp_path):
    repo, git, bare = make_primary_with_origin(tmp_path)
    assert ptg.behind_count(str(repo), "main") == 0


def test_behind_under_24h_does_not_page(tmp_path, monkeypatch):
    repo, git, bare = make_primary_with_origin(tmp_path)
    advance_origin(bare, repo, git, tmp_path)
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    assert ptg.check_behind_age(repos=[str(repo)], state=state) == []
    assert alerts == [] and str(repo) in state


def test_behind_over_24h_pages_once_then_repeats_daily(tmp_path, monkeypatch):
    repo, git, bare = make_primary_with_origin(tmp_path)
    advance_origin(bare, repo, git, tmp_path)
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append((t, m)))
    state = {}
    now = time.time()

    stale = ptg.check_behind_age(repos=[str(repo)], state=state, now=now + 25 * 3600)
    assert len(stale) == 1 and stale[0]["paged"] and stale[0]["behind"] == 1
    assert len(alerts) == 1 and "wq#2618" in alerts[0][0]
    assert "ffprimary" in alerts[0][1]

    stale = ptg.check_behind_age(repos=[str(repo)], state=state, now=now + 26 * 3600)
    assert len(stale) == 1 and not stale[0]["paged"] and len(alerts) == 1

    ptg.check_behind_age(repos=[str(repo)], state=state, now=now + 50 * 3600)
    assert len(alerts) == 2


def test_fast_forwarding_clears_the_behind_episode(tmp_path, monkeypatch):
    repo, git, bare = make_primary_with_origin(tmp_path)
    advance_origin(bare, repo, git, tmp_path)
    monkeypatch.setattr(ptg, "alert", lambda t, m: None)
    state = {str(repo): {"since": 0, "paged": 0}}
    git("merge", "-q", "--ff-only", "origin/main")
    assert ptg.check_behind_age(repos=[str(repo)], state=state, now=10 * 86400) == []
    assert state == {}


def test_cli_check_pages_stale_untracked_and_behind(tmp_path, monkeypatch, capsys):
    repo, git, bare = make_primary_with_origin(tmp_path)
    advance_origin(bare, repo, git, tmp_path)
    (repo / "drift_plan.py").write_text("deploy artifact\n")
    old = time.time() - 2 * 86400
    os.utime(repo / "drift_plan.py", (old, old))
    monkeypatch.setattr(ptg, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(ptg, "UNTRACKED_STATE_PATH",
                        str(tmp_path / "untracked-preseeded.json"))
    monkeypatch.setattr(ptg, "BEHIND_STATE_PATH",
                        str(tmp_path / "behind-preseeded.json"))
    ptg._save_state({str(repo): {"since": old, "paged": 0}},
                    str(tmp_path / "untracked-preseeded.json"))
    ptg._save_state({str(repo): {"since": old, "paged": 0}},
                    str(tmp_path / "behind-preseeded.json"))
    alerts = []
    monkeypatch.setattr(ptg, "alert", lambda t, m: alerts.append(t))

    assert ptg.main(["check", "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "UNTRACKED" in out and "BEHIND" in out
    assert "PRIMARY tree has untracked files >24h (wq#2618)" in alerts
    assert "PRIMARY tree behind origin >24h (wq#2618)" in alerts
