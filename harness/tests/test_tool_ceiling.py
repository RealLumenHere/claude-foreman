"""wq#4634: ceiling-killed Bash calls — detection, re-issue under watchdog, resume.

Test-to-failure matrix (harness/TOOL_CEILING_REISSUE.md §6): every recovery branch is
driven to its failure mode — watchdog refuses, watchdog vanishes without a verdict,
command on the never-reissue list, detection budget exhausted — and the negative
controls prove the detector cannot fire on an ordinary failing command, on the phrase
mid-output, or on a successful result. The real-binary tests drive /home/lumen/bin/
watchdog itself to OK, FAIL and TIMEOUT verdicts."""
import json
import os
import shutil
import stat
import sys
import textwrap
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tool_ceiling
import fworker

CEILING_ERR = "Command timed out after 2m 0.0s\npartial output here"


def frames(command="sleep 300", err=CEILING_ERR, is_error=True, tool="Bash"):
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": tool,
             "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error,
             "content": [{"type": "text", "text": err}]}]}},
    ]


def feed(tracker, fs):
    fired = False
    for f in fs:
        fired = tracker.observe(f) or fired
    return fired


# ---- detection -----------------------------------------------------------------------

def test_ceiling_kill_detected():
    t = tool_ceiling.CeilingTracker(budget=2)
    assert feed(t, frames()) is True
    assert t.hit["class"] == "bash_timeout"
    assert t.hit["command"] == "sleep 300"
    assert "timed out" in t.hit["error"]


def test_ordinary_failure_not_detected():
    t = tool_ceiling.CeilingTracker(budget=2)
    assert feed(t, frames(err="adb: no devices found")) is False
    assert t.hit is None


def test_phrase_mid_output_not_detected():
    # a failing test suite that PRINTS the banner must not look like a ceiling kill
    t = tool_ceiling.CeilingTracker(budget=2)
    assert feed(t, frames(err="FAIL: probe says Command timed out after 2m 0.0s")) is False


def test_successful_result_not_detected():
    t = tool_ceiling.CeilingTracker(budget=2)
    assert feed(t, frames(is_error=False)) is False


def test_non_bash_tool_not_detected():
    t = tool_ceiling.CeilingTracker(budget=2)
    assert feed(t, frames(tool="mcp__keenable__search")) is False


def test_budget_exhausted_disables_detection():
    t = tool_ceiling.CeilingTracker(budget=0)
    assert feed(t, frames()) is False
    assert t.hit is None


def test_extension_patterns_via_env(tmp_path, monkeypatch):
    pat = tmp_path / "classes.json"
    pat.write_text(json.dumps([{"class": "mcp_abort", "tool_re": r"\Amcp__",
                                "error_re": r"MCP error -32001"}]))
    monkeypatch.setenv(tool_ceiling.KILL_PATTERNS_ENV, str(pat))
    t = tool_ceiling.CeilingTracker(budget=1)
    assert feed(t, frames(tool="mcp__commander__start_process",
                          err="MCP error -32001: Request timed out")) is True
    assert t.hit["class"] == "mcp_abort"


def test_malformed_extension_file_keeps_builtins(tmp_path, monkeypatch):
    pat = tmp_path / "classes.json"
    pat.write_text("{not json")
    monkeypatch.setenv(tool_ceiling.KILL_PATTERNS_ENV, str(pat))
    t = tool_ceiling.CeilingTracker(budget=1)
    assert feed(t, frames()) is True


def test_never_reissue_list():
    hits = ["git push origin main", "cd /x && git push",
            "/home/lumen/bin/lumen-pr land", "lumen-pr commit -m x",
            "/home/lumen/bin/wq done 4634 --notes x", "wq release 4634"]
    misses = ["grep push file", "echo wq done", "git log", "make build && make push-img"]
    for c in hits:
        assert tool_ceiling.NEVER_REISSUE_RE.search(c), c
    for c in misses:
        assert not tool_ceiling.NEVER_REISSUE_RE.search(c), c


# ---- reissue with a stub watchdog (failure injection) --------------------------------

def stub_watchdog(tmp_path, body):
    p = tmp_path / "watchdog"
    p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def hit(command="sleep 300"):
    return {"class": "bash_timeout", "tool": "Bash",
            "command": command, "error": CEILING_ERR}


def reissue(tmp_path, wd, command="sleep 300", **kw):
    kw.setdefault("timeout_min", 0.05)       # 3s re-run ceiling for tests
    kw.setdefault("wait_slack_s", 5)
    kw.setdefault("poll_s", 1)
    return tool_ceiling.reissue(
        hit(command), label=f"tctest-{time.time_ns()}", cwd=str(tmp_path),
        env=dict(os.environ), run_dir_path=str(tmp_path),
        watchdog_bin=wd, log=lambda m: None, **kw)


def record_of(tmp_path):
    recs = list(tmp_path.glob("ceiling-reissue-*.json"))
    assert len(recs) == 1
    return json.loads(recs[0].read_text())


def test_reissue_happy_path_verdict(tmp_path):
    # stub daemonises nothing: writes result.json immediately, exits 0 like the real
    # daemonizer, and exercises label parsing + the result wait loop.
    wd = stub_watchdog(tmp_path, """\
        label=""
        while [ $# -gt 0 ]; do
          case "$1" in --label) label=$2; shift 2;; --) shift; break;; *) shift;; esac
        done
        printf '{"status":"OK","rc":0,"note":"exit 0 after 12s","out":"/tmp/o","tail":"/tmp/t"}' \\
          > "/tmp/watchdog-$label.result.json"
        echo "watchdog started: label=$label"
        """)
    v = reissue(tmp_path, wd)
    assert "verdict: OK (exit 0)" in v
    assert "Do NOT re-run" in v
    assert "sleep 300" in v
    assert record_of(tmp_path)["status"] == "reissued"


def test_reissue_watchdog_refuses(tmp_path):
    wd = stub_watchdog(tmp_path, 'echo "watchdog: usage error" >&2; exit 3\n')
    v = reissue(tmp_path, wd)
    assert "could not" in v or "refused" in v
    assert "Verify the command's effects yourself" in v
    assert record_of(tmp_path)["status"] == "spawn_failed"


def test_reissue_watchdog_missing(tmp_path):
    v = reissue(tmp_path, str(tmp_path / "no-such-watchdog"))
    assert "Verify the command's effects yourself" in v
    assert record_of(tmp_path)["status"] == "spawn_failed"


def test_reissue_verdict_never_arrives(tmp_path):
    # TEST-TO-FAILURE: daemonizer exits 0 but no result.json ever lands (watchdog
    # supervisor died). reissue must give up at timeout+slack and hand back a
    # still-running advisory naming the files to poll — never hang, never return None.
    wd = stub_watchdog(tmp_path, 'echo started\n')
    t0 = time.time()
    v = reissue(tmp_path, wd, timeout_min=0.02, wait_slack_s=2)
    assert time.time() - t0 < 30
    assert "STILL" in v and "result.json" in v
    assert record_of(tmp_path)["status"] == "wait_timeout"


def test_reissue_denylisted_command_not_rerun(tmp_path):
    # the stub would create a marker if invoked — it must not be
    wd = stub_watchdog(tmp_path, f'touch {tmp_path}/INVOKED\n')
    v = reissue(tmp_path, wd, command="git push origin main")
    assert "NOT re-run" in v
    assert not (tmp_path / "INVOKED").exists()
    assert record_of(tmp_path)["status"] == "not_reissued"


def test_reissue_no_command_returns_none(tmp_path):
    assert tool_ceiling.reissue({"class": "x"}, label="tctest-none",
                                cwd=str(tmp_path), env={}, run_dir_path=str(tmp_path),
                                log=lambda m: None) is None


# ---- run_child integration (template: test_loop_break_integration.py) ---------------

def make_child(tmp_path, err):
    script = textwrap.dedent(f"""\
        import json, sys, time
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "tc-int",
              "model": "fake"}})
        emit({{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "id": "t0", "name": "Bash",
              "input": {{"command": "sleep 300"}}}}]}}}})
        emit({{"type": "user", "message": {{"content": [
            {{"type": "tool_result", "tool_use_id": "t0", "is_error": True,
              "content": [{{"type": "text", "text": {err!r}}}]}}]}}}})
        time.sleep(0.2)
        emit({{"type": "result", "subtype": "success", "is_error": False}})
        time.sleep(0.5)
    """)
    p = tmp_path / "child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def run(tmp_path, err):
    tracker = tool_ceiling.CeilingTracker(budget=2)
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_child(tmp_path, err), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999998, attempt=1,
        frame_gap_s=10, post_result_grace_s=2, ceiling_tracker=tracker)
    return tracker, state, killed_reason, exit_code


def test_run_child_stops_on_ceiling_kill(tmp_path):
    tracker, state, killed_reason, exit_code = run(tmp_path, CEILING_ERR)
    assert killed_reason == "tool_ceiling"
    assert tracker.hit["command"] == "sleep 300"
    assert state.session_id == "tc-int"     # the resume handle survives the kill
    assert exit_code != 0


def test_run_child_ordinary_failure_untouched(tmp_path):
    tracker, state, killed_reason, exit_code = run(tmp_path, "make: *** [all] Error 2")
    assert killed_reason in (None, "post_result_stuck")
    assert tracker.hit is None


# ---- the real watchdog binary (test-to-failure of the re-run itself) -----------------

REAL_WD = os.environ.get("FOREMAN_WATCHDOG_BIN", "/home/lumen/bin/watchdog")
import pytest

real_wd = pytest.mark.skipif(
    not (os.access(REAL_WD, os.X_OK) and shutil.which("jq") and shutil.which("setsid")),
    reason="real watchdog (or jq/setsid) not available on this host")


@real_wd
def test_real_watchdog_ok(tmp_path):
    v = reissue(tmp_path, REAL_WD, command="echo wq4634-ok", timeout_min=0.2,
                wait_slack_s=30, poll_s=1)
    assert "verdict: OK (exit 0)" in v
    res = record_of(tmp_path)["result"]
    assert res["rc"] == 0 and res["status"] == "OK"


@real_wd
def test_real_watchdog_command_fails(tmp_path):
    v = reissue(tmp_path, REAL_WD, command="exit 7", timeout_min=0.2,
                wait_slack_s=30, poll_s=1)
    assert "verdict: FAIL (exit 7)" in v


@real_wd
def test_real_watchdog_rerun_times_out(tmp_path):
    # the re-run itself hits watchdog's ceiling: verdict TIMEOUT, process group killed
    v = reissue(tmp_path, REAL_WD, command="sleep 60", timeout_min=0.05,
                wait_slack_s=60, poll_s=1)
    assert "verdict: TIMEOUT" in v
    assert record_of(tmp_path)["result"]["status"] == "TIMEOUT"
