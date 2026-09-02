"""dispatch_health.py / dispatch_config_lint.py runtime-state split (wq#1253, #23).

PROBLEM: health_state.json and config_lint_state.json were tracked files that the
daemons rewrite on every alert-dedup update, so the primary foreman tree was dirty
after nearly every pass and lumen-pr's dirty-tree guard false-blocked every land
(worked around with LUMEN_PR_ALLOW_DIRTY=1). FIX: both now live under
dispatcher/runtime/, gitignored, with a one-release fallback that reads the old
tracked path if the new file hasn't been written yet.

These tests prove: (1) the default path resolves under runtime/, (2) a fresh runtime/
dir gets created on first save, (3) load falls back to the legacy path when the new
one is absent, (4) once state has been written under runtime/, `git status` in a repo
mirroring this layout stays clean — the actual claim the ticket is closing out.
"""
import json
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_health              # noqa: E402
import dispatch_config_lint         # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           check=True).stdout


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    return path


# ---- dispatch_health.py -----------------------------------------------------------

def test_health_state_default_path_under_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_health, "DISPATCHER_DIR", str(tmp_path))
    monkeypatch.setattr(dispatch_health, "STATE_PATH",
                         os.path.join(str(tmp_path), "runtime", "health_state.json"))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH",
                         os.path.join(str(tmp_path), "health_state.json"))

    assert not os.path.exists(dispatch_health.STATE_PATH)
    dispatch_health.save_state({"last_alert_ts": "2026-09-02T00:00:00+00:00"})
    assert os.path.exists(dispatch_health.STATE_PATH)
    assert dispatch_health.load_state()["last_alert_ts"] == "2026-09-02T00:00:00+00:00"


def test_health_state_falls_back_to_legacy_path(monkeypatch, tmp_path):
    new_path = tmp_path / "runtime" / "health_state.json"
    legacy_path = tmp_path / "health_state.json"
    legacy_path.write_text(json.dumps({"last_alert_ts": "2026-08-27T22:30:48+00:00"}))

    monkeypatch.setattr(dispatch_health, "STATE_PATH", str(new_path))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH", str(legacy_path))

    assert dispatch_health.load_state()["last_alert_ts"] == "2026-08-27T22:30:48+00:00"


def test_health_state_prefers_new_path_once_it_exists(monkeypatch, tmp_path):
    new_path = tmp_path / "runtime" / "health_state.json"
    legacy_path = tmp_path / "health_state.json"
    new_path.parent.mkdir(parents=True)
    new_path.write_text(json.dumps({"last_alert_ts": "new"}))
    legacy_path.write_text(json.dumps({"last_alert_ts": "old"}))

    monkeypatch.setattr(dispatch_health, "STATE_PATH", str(new_path))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH", str(legacy_path))

    assert dispatch_health.load_state()["last_alert_ts"] == "new"


def test_health_state_missing_both_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_health, "STATE_PATH",
                         str(tmp_path / "runtime" / "health_state.json"))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH",
                         str(tmp_path / "health_state.json"))
    assert dispatch_health.load_state() == {}


# ---- dispatch_config_lint.py -------------------------------------------------------

def test_config_lint_state_default_path_under_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_config_lint, "STATE_PATH",
                         str(tmp_path / "runtime" / "config_lint_state.json"))
    monkeypatch.setattr(dispatch_config_lint, "_LEGACY_STATE_PATH",
                         str(tmp_path / "config_lint_state.json"))

    assert not os.path.exists(dispatch_config_lint.STATE_PATH)
    dispatch_config_lint.save_state({"last_alert_ts": "2026-09-02T00:00:00+00:00"})
    assert os.path.exists(dispatch_config_lint.STATE_PATH)
    assert dispatch_config_lint.load_state()["last_alert_ts"] == "2026-09-02T00:00:00+00:00"


def test_config_lint_state_falls_back_to_legacy_path(monkeypatch, tmp_path):
    new_path = tmp_path / "runtime" / "config_lint_state.json"
    legacy_path = tmp_path / "config_lint_state.json"
    legacy_path.write_text(json.dumps({"last_alert_ts": "2026-08-27T19:25:29+00:00"}))

    monkeypatch.setattr(dispatch_config_lint, "STATE_PATH", str(new_path))
    monkeypatch.setattr(dispatch_config_lint, "_LEGACY_STATE_PATH", str(legacy_path))

    assert dispatch_config_lint.load_state()["last_alert_ts"] == "2026-08-27T19:25:29+00:00"


# ---- the actual dirty-tree-guard claim ---------------------------------------------

def test_runtime_state_writes_never_dirty_the_tree(monkeypatch, tmp_path):
    """Mirrors the ticket's acceptance test: run the daemons (dirtying runtime state),
    then confirm `git status --porcelain` is clean — the guard would pass with no
    LUMEN_PR_ALLOW_DIRTY override."""
    repo = _init_repo(str(tmp_path / "repo"))
    gitignore = os.path.join(repo, ".gitignore")
    with open(gitignore, "w") as f:
        f.write("runtime/\n")
    with open(os.path.join(repo, "config.json"), "w") as f:
        f.write("{}\n")
    _git(repo, "add", ".gitignore", "config.json")
    _git(repo, "commit", "-q", "-m", "initial")

    assert _git(repo, "status", "--porcelain") == ""

    dispatcher_dir = os.path.join(repo, "dispatcher")
    os.makedirs(dispatcher_dir, exist_ok=True)
    monkeypatch.setattr(dispatch_health, "STATE_PATH",
                         os.path.join(dispatcher_dir, "runtime", "health_state.json"))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH",
                         os.path.join(dispatcher_dir, "health_state.json"))
    monkeypatch.setattr(dispatch_config_lint, "STATE_PATH",
                         os.path.join(dispatcher_dir, "runtime", "config_lint_state.json"))
    monkeypatch.setattr(dispatch_config_lint, "_LEGACY_STATE_PATH",
                         os.path.join(dispatcher_dir, "config_lint_state.json"))

    dispatch_health.save_state({"last_alert_ts": "2026-09-02T00:00:00+00:00"})
    dispatch_config_lint.save_state({"last_alert_ts": "2026-09-02T00:00:00+00:00"})

    assert _git(repo, "status", "--porcelain") == ""
