"""primary_tree_guard tests — wq#900 adversarial verify.

Zero quota. Simulates the audited failure (a worker's raw `git checkout` left a
PRIMARY tree on a feature branch) against real throwaway git repos and asserts
detect + alert + restore-only-when-clean. The guard.sh deny half of wq#900 is
covered in test_guard.py.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import primary_tree_guard as ptg


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
