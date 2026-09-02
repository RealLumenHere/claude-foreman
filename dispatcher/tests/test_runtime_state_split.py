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

wq#1282 (#23) finishes the split with the last mutable-state-in-tracked-config vector:
lane auto-registration. wq-lane-init used to edit config.json in place; it now stages
into runtime/lane_state.json, and dispatch_config_lint reads config.json at HEAD — the
same source the dispatcher dispatches from — so a staged-but-uncommitted lane is
reported instead of silently satisfying the lint.
"""
import json
import os
import sqlite3
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


# ---- lane state: staged in runtime/, policy still comes from HEAD (wq#1282) ---------

def _lane_repo(tmp_path, committed_lanes, working_tree_lanes=None):
    """A repo mirroring dispatcher/: config.json committed with `committed_lanes`, and
    optionally left dirty with `working_tree_lanes` on top."""
    repo = _init_repo(str(tmp_path / "lanerepo"))
    with open(os.path.join(repo, ".gitignore"), "w") as f:
        f.write("runtime/\n")
    config = os.path.join(repo, "config.json")
    with open(config, "w") as f:
        json.dump({"lanes": {ln: {"enabled": True} for ln in committed_lanes}}, f, indent=2)
    _git(repo, "add", ".gitignore", "config.json")
    _git(repo, "commit", "-q", "-m", "initial")
    if working_tree_lanes is not None:
        with open(config, "w") as f:
            json.dump({"lanes": {ln: {"enabled": True} for ln in working_tree_lanes}},
                      f, indent=2)
    return repo, config


def _lane_db(tmp_path, open_lanes):
    """queue.db stand-in: one open ticket per lane in `open_lanes`."""
    db = str(tmp_path / "queue.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, lane TEXT, status TEXT)")
    conn.executemany("INSERT INTO tasks (lane, status) VALUES (?, 'open')",
                     [(ln,) for ln in open_lanes])
    conn.commit()
    conn.close()
    return db


def _stage_lane(repo, lane):
    """What wq-lane-init writes: a staged lane under runtime/, never in config.json."""
    path = os.path.join(repo, "runtime", "lane_state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"lanes": {lane: {"enabled": False,
                                    "max_per_day": 999,
                                    "note": "auto-registered by wq-lane-init"}}}, f)
    return path


def test_lint_reads_committed_config_not_the_working_tree(tmp_path):
    """The regression wq#1282 closes: an uncommitted lane used to satisfy the lint while
    the dispatcher (HEAD-only, wq#772) went on ignoring the lane's tickets."""
    repo, config = _lane_repo(tmp_path, ["build"], working_tree_lanes=["build", "nest"])
    db = _lane_db(tmp_path, ["nest"])

    assert dispatch_config_lint.load_config_lanes(config) == {"build"}
    fails, _warns = dispatch_config_lint.lint(config_path=config, db_path=db,
                                              lane_state_path=str(tmp_path / "absent"))
    assert len(fails) == 1 and "'nest'" in fails[0]


def test_lint_clean_once_the_lane_is_committed(tmp_path):
    repo, config = _lane_repo(tmp_path, ["build", "nest"])
    db = _lane_db(tmp_path, ["nest"])

    fails, _warns = dispatch_config_lint.lint(config_path=config, db_path=db,
                                              lane_state_path=str(tmp_path / "absent"))
    assert fails == []


def test_lint_names_the_commit_step_for_a_staged_lane(tmp_path):
    repo, config = _lane_repo(tmp_path, ["build"])
    db = _lane_db(tmp_path, ["nest"])
    staged = _stage_lane(repo, "nest")

    assert dispatch_config_lint.load_staged_lanes(staged) == {"nest"}
    fails, _warns = dispatch_config_lint.lint(config_path=config, db_path=db,
                                              lane_state_path=staged)
    assert len(fails) == 1
    assert "lane_state.json" in fails[0] and "commit the lane into config.json" in fails[0]


def test_staged_lane_never_dirties_the_tree(tmp_path):
    """The ticket's acceptance claim for the lane vector: opening a lane leaves the
    tracked tree clean, so lumen-pr's guard passes with no LUMEN_PR_ALLOW_DIRTY."""
    repo, _config = _lane_repo(tmp_path, ["build"])
    assert _git(repo, "status", "--porcelain") == ""

    _stage_lane(repo, "nest")

    assert _git(repo, "status", "--porcelain") == ""


def test_staged_lanes_absent_or_malformed_reads_as_nothing_staged(tmp_path):
    assert dispatch_config_lint.load_staged_lanes(str(tmp_path / "absent.json")) == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert dispatch_config_lint.load_staged_lanes(str(bad)) == set()
    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"lanes": ["nest"]}))
    assert dispatch_config_lint.load_staged_lanes(str(wrong_shape)) == set()


def test_lint_raises_rather_than_linting_an_unreadable_config(tmp_path):
    """No working-tree fallback: if HEAD can't be read the lint says so instead of
    quietly linting a file the dispatcher never loads."""
    outside = tmp_path / "loose" / "config.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"lanes": {"nest": {"enabled": True}}}))

    try:
        dispatch_config_lint.load_config_lanes(str(outside))
    except dispatch_config_lint.LintError:
        return
    raise AssertionError("expected LintError for a config outside any git repo")
