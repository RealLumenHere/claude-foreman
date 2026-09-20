"""CAL-1 fixture tests (wq#619): scratch build/reset, usage sampling, models.yaml apply,
and one serial matrix cell end-to-end against the fake claude shim (zero quota)."""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import cal1_fixture as cal  # noqa: E402

MODELS = """models:
  - id: claude-sonnet-5
    burn:
      est: "x"
      measured_tokens: null            # caveat
      measured: "old"
  - id: claude-other
    burn:
      measured_tokens: null
"""


def write_state(path, session, weekly=56):
    with open(path, "w") as f:
        json.dump({"limits": {"session": {"percent": session}, "weekly_all": {"percent": weekly}}}, f)


def test_build_creates_scratch_repo_and_board(tmp_path):
    p = cal.build(str(tmp_path / "fx"))
    assert os.path.isdir(os.path.join(p["repo"], ".git"))
    assert all(os.path.isdir(p[k]) for k in ("events", "runs", "worktrees"))
    log = subprocess.run(["git", "log", "--oneline"], cwd=p["repo"], capture_output=True, text=True)
    assert len(log.stdout.strip().splitlines()) == 1


def test_reset_restores_repo_and_drops_db(tmp_path):
    root = str(tmp_path / "fx")
    p = cal.build(root)
    with open(os.path.join(p["repo"], "README.md"), "a") as f:
        f.write("dirty\n")
    subprocess.run(["git", "commit", "-qam", "second"], cwd=p["repo"], check=True)
    subprocess.run(["git", "branch", "stray"], cwd=p["repo"], check=True)
    open(p["wq_db"], "w").close()
    open(os.path.join(p["runs"], "junk"), "w").close()
    cal.reset(root)
    log = subprocess.run(["git", "log", "--oneline"], cwd=p["repo"], capture_output=True, text=True)
    assert len(log.stdout.strip().splitlines()) == 1
    branches = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=p["repo"],
                              capture_output=True, text=True).stdout.split()
    assert branches == ["main"]
    assert not os.path.exists(p["wq_db"])
    assert os.listdir(p["runs"]) == []


def test_usage_sampling_and_delta(tmp_path):
    st = str(tmp_path / "state.json")
    write_state(st, 43)
    before = cal.read_usage(st)
    write_state(st, 46)
    assert cal.usage_delta(before, cal.read_usage(st)) == {"session": 3, "weekly_all": 0}


def test_sum_tokens_walks_nested_records():
    rec = {"usage": {"input_tokens": 10, "output_tokens": 5}, "x": [{"output_tokens": 7}]}
    assert cal.sum_tokens(rec) == 22
    assert cal.sum_tokens({"a": 1}) is None


def test_apply_writes_measured_usage_and_is_idempotent(tmp_path):
    res = tmp_path / "res.json"
    res.write_text(json.dumps({"results": [
        {"model": "claude-sonnet-5", "effort": "low", "exit_code": 0, "tokens": 1000,
         "pct_delta": {"session": 2.0}},
        {"model": "claude-other", "effort": None, "exit_code": 1, "tokens": None, "pct_delta": {}},
    ]}))
    y = tmp_path / "models.yaml"
    y.write_text(MODELS)
    assert cal.apply_measured(str(res), str(y)) == ["claude-sonnet-5"]
    cal.apply_measured(str(res), str(y))
    text = y.read_text()
    assert text.count("measured_usage:") == 1
    assert "measured_tokens: 1000" in text
    assert "low: {pct_delta: {session: +2}, tokens: 1000, n: 1}" in text
    assert "claude-other" in text and text.count("measured_tokens: null") == 1  # failed run not applied


def test_matrix_cell_end_to_end_with_fake_claude(tmp_path, monkeypatch):
    root = str(tmp_path / "fx")
    st = str(tmp_path / "usage.json")
    write_state(st, 40)
    wq = "/home/lumen/bin/wq"
    for k, v in {"FAKE_CLAUDE_SCENARIO": "success", "FAKE_CLAUDE_TICKET": "1",
                 "FAKE_CLAUDE_GUARD_SH": os.path.join(HARNESS_DIR, "guard.sh"),
                 "FAKE_CLAUDE_WQ_BIN": wq,
                 "FOREMAN_GOVERNOR_CLI": os.path.join(TESTS_DIR, "fake_governor.py"),
                 "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent")}.items():
        monkeypatch.setenv(k, v)
    rs = cal.run_matrix(root, ["claude-sonnet-5@low"], st, 0,
                        claude_bin=os.path.join(TESTS_DIR, "fake_claude.py"))
    assert rs[0]["exit_code"] == 0 and rs[0]["cell"] == "claude-sonnet-5@low"
    assert rs[0]["pct_delta"] == {"session": 0, "weekly_all": 0}
    saved = json.load(open(cal.paths(root)["results"]))
    assert saved["results"][0]["ticket"] == 1
