"""ISSUE-SOLUTIONS-fable.md §2c — harness-preflight + dispatch stamp gate. Zero quota:
the live-proof layer's `claude -p` spawn is stubbed via the injectable `runner` param,
same pattern fworker's own tests use to avoid spending Anthropic quota in CI.
"""
import json
import os
import shutil
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import harness_preflight  # noqa: E402
import permission_profiles  # noqa: E402

SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")
FIXTURE_REPO = "/home/lumen/wq1259-ts-fixture-repo"
FIXTURE_NAME = "wq1259-ts-fixture-repo"


def _clean_runner(cmd, cwd):
    """Simulates a live proof with zero permission denials."""
    return [{"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": False, "content": "ok"}]}}]


def _denying_runner(cmd, cwd):
    return [{"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True,
         "content": "Claude requested permissions to use Bash, but you haven't granted "
                     "it yet. This command requires approval: npx tsc --noEmit"}]}}]


@pytest.fixture
def fixture_conf(tmp_path):
    conf_path = os.path.join(permission_profiles.REPOS_DIR, f"{FIXTURE_NAME}.json")
    stamp_dir = str(tmp_path / "stamps")
    render_dir = str(tmp_path / "render")

    def write(profiles, extra_allow=None):
        with open(conf_path, "w") as f:
            json.dump({"profiles": profiles, "extra_allow": extra_allow or [],
                       "preflight_cmds": ["npx tsc --noEmit"], "verbs": []}, f)

    yield write, stamp_dir, render_dir
    if os.path.isfile(conf_path):
        os.remove(conf_path)
    shutil.rmtree(stamp_dir, ignore_errors=True)
    shutil.rmtree(render_dir, ignore_errors=True)


def test_split_segments_respects_pipes_and_quotes():
    assert harness_preflight.split_segments("git status | tail -5 | grep -c .") == [
        "git status", "tail -5", "grep -c ."]
    assert harness_preflight.split_segments("npm test && echo done") == [
        "npm test", "echo done"]


def test_static_lint_fails_naming_missing_npx_rule(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)

    assert result.ok is False
    assert result.reason == "static lint failed"
    assert any("npx" in f for f in result.static_failures)
    assert harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir) is None


def test_both_layers_pass_and_stamp_written_once_node_ts_added(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)

    assert result.ok is True
    assert result.static_failures == []
    assert result.denials == []
    stamp = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)
    assert stamp is not None
    assert stamp["hash"] == result.digest

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert (ok, reason) == (True, None)


def test_live_proof_denials_block_stamp_even_when_static_lint_clean(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_denying_runner)

    assert result.ok is False
    assert result.reason == "live proof found permission denials"
    assert any("requires approval" in d for d in result.denials)
    assert harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir) is None


def test_dispatch_gate_refuses_unstamped_repo(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False
    assert "no preflight stamp" in reason


def test_dispatch_gate_refuses_until_reproof_after_mutation(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)
    assert result.ok is True

    # mutate the repo's declared settings (extra_allow) without re-running preflight
    write(["base", "python", "node-ts"], extra_allow=["Bash(some-new-tool *)"])

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False
    assert "stale preflight stamp" in reason

    # re-proof restores a fresh stamp
    result2 = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)
    assert result2.ok is True
    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert (ok, reason) == (True, None)


def test_unmigrated_repo_is_not_gated():
    ok, reason = harness_preflight.dispatch_gate_check("/home/lumen/no-such-repo-configured")
    assert (ok, reason) == (True, None)
    result = harness_preflight.preflight("/home/lumen/no-such-repo-configured")
    assert result.ok is False
    assert "not migrated to profiles" in result.reason


def test_preflight_commands_include_fleet_exec_probes(fixture_conf):
    """wq#1332 (§3.2 point 4): one representative command per v1 fleet-exec verb is
    always proved, repo-agnostic, the same way INSPECTION_PIPE_PROBE always is —
    the allow rule is unconditional, so this doesn't need a needs:-tagged repo."""
    write, _stamp_dir, _render_dir = fixture_conf
    write(["node"])
    conf = harness_preflight.load_repo_config(FIXTURE_REPO)
    cmds = harness_preflight.preflight_commands(FIXTURE_REPO, conf)
    for probe in harness_preflight.FLEET_EXEC_PREFLIGHT_PROBES:
        assert probe in cmds


def test_fleet_exec_probes_pass_static_lint_against_real_worker_settings():
    """Proves the worker-settings.json allow rule this PR adds actually covers the
    fleet-exec shapes harness-preflight now proves for every needs:-tagged repo."""
    with open(SETTINGS) as f:
        settings = json.load(f)
    perms = settings["permissions"]
    failures = harness_preflight.static_lint(
        harness_preflight.FLEET_EXEC_PREFLIGHT_PROBES, perms["allow"], perms.get("deny", []))
    assert failures == []
