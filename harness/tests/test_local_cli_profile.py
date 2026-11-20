"""wq#2185 (dispatch rebuild 4b): the local-rung CLI profile (tool/MCP restriction, one slot
per worker), the calibrated size gate, the first-frame budget refusal and the charging
nuance for a kill the harness caused.
"""
import json
import os
import shutil
import warnings
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import charging  # noqa: E402
import cli_calibration  # noqa: E402
import fworker  # noqa: E402

from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, run_fworker,
)

LOCAL = "hathor-llama8b"
PROFILE_FLAGS = ["--tools", "Bash,Read,Edit,Write,Grep,Glob", "--strict-mcp-config",
                 "--exclude-dynamic-system-prompt-sections"]


def _cmd(**kw):
    return fworker.build_claude_cmd("do it", LOCAL, None, "/s.json", ["/d"], **kw)


# ---------- command line ----------

def test_local_profile_adds_the_three_flags_and_keeps_the_hooks():
    cmd = _cmd(local_profile=True)
    i = cmd.index("--tools")
    assert cmd[i:i + 2] == PROFILE_FLAGS[:2]
    assert all(f in cmd for f in PROFILE_FLAGS)
    # `--bare` would skip fworker's action gate and stop gate hooks
    assert "--bare" not in cmd
    assert "--settings" in cmd and "--include-hook-events" in cmd


def test_profile_flags_are_absent_by_default_for_cloud():
    cmd = fworker.build_claude_cmd("do it", "claude-sonnet-5", "high", "/s.json", [])
    assert not {"--tools", "--strict-mcp-config",
                "--exclude-dynamic-system-prompt-sections"} & set(cmd)


def test_the_tool_set_is_the_two_local_archetypes_minimum():
    assert set(fworker.LOCAL_CLI_TOOLS.split(",")) == {
        "Bash", "Read", "Edit", "Write", "Grep", "Glob"}


@pytest.mark.parametrize("model,expect", [
    (LOCAL, True), ("nephthys-qwen14b", True), ("qwen36-local", True), ("local-worker", True),
    ("deepseek-v4-flash", False), ("claude-sonnet-5", False), ("", False)])
def test_profile_applies_to_local_rung_workers_only(model, expect):
    assert fworker.local_cli_profile(model) is expect


@pytest.mark.parametrize("lever", [("FOREMAN_CHARGE_MODE", "legacy"),
                                   ("FOREMAN_LOCAL_CLI_PROFILE", "off")])
def test_both_levers_switch_the_profile_off(monkeypatch, lever):
    assert fworker.local_cli_profile(LOCAL)
    monkeypatch.setenv(*lever)
    assert not fworker.local_cli_profile(LOCAL)


def test_profile_env_routes_the_background_model_and_stops_the_title_call(tmp_path):
    prof = fworker.local_profile_env()
    assert prof["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"
    assert prof["ANTHROPIC_SMALL_FAST_MODEL"] == prof["ANTHROPIC_DEFAULT_HAIKU_MODEL"] \
        == "deepseek-v4-flash"
    assert not fworker.is_local_rung(prof["ANTHROPIC_SMALL_FAST_MODEL"])  # never a Mac slot
    with_env = fworker.build_env("w", LOCAL, "", str(tmp_path), 1, cli_profile=True)
    without = fworker.build_env("w", LOCAL, "", str(tmp_path), 1)
    assert all(with_env.get(k) == v for k, v in prof.items())
    assert not set(prof) & {k for k in without if k not in os.environ}


def test_the_child_really_gets_the_profile_flags_and_env(tmp_path):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-" + "test-foreman-key")
    dump = tmp_path / "child.json"
    env, *_ = make_scratch_env(tmp_path, 0, "success", extra={
        "FOREMAN_LITELLM_KEY_FILE": str(key_file), "FAKE_CLAUDE_ENV_DUMP": str(dump)})
    ticket_id = wq_add(env, "scratch local profile ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, "success", model=LOCAL, effort="",
                         archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    got = json.loads(dump.read_text())
    assert all(f in got["_argv"] for f in PROFILE_FLAGS)
    assert got["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"
    assert got["ANTHROPIC_SMALL_FAST_MODEL"] == "deepseek-v4-flash"


@pytest.mark.parametrize("extra_env", [{"FOREMAN_LOCAL_CLI_PROFILE": "off"},
                                       {"FOREMAN_CHARGE_MODE": "legacy"}])
def test_under_the_lever_the_child_gets_neither_flags_nor_env(tmp_path, extra_env):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-" + "test-foreman-key")
    dump = tmp_path / "child.json"
    env, *_ = make_scratch_env(tmp_path, 0, "success", extra=dict(
        extra_env, FOREMAN_LITELLM_KEY_FILE=str(key_file), FAKE_CLAUDE_ENV_DUMP=str(dump)))
    ticket_id = wq_add(env, "scratch profile-off ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, "success", model=LOCAL, effort="",
                         archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    got = json.loads(dump.read_text())
    assert not {"--tools", "--strict-mcp-config"} & set(got["_argv"])
    assert "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" not in got
    assert "ANTHROPIC_SMALL_FAST_MODEL" not in got


# ---------- calibration: the constant is pinned to a CLI version and re-measurable ----------

needs_cli = pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")


@needs_cli
def test_recorded_cli_version_matches_the_installed_cli():
    """wq#5170: the CLI auto-updates (2.1.296 -> 2.1.328 -> 2.1.329 inside one evening), and
    this suite gates every `lumen-pr land` (wq#5133), so an exact-version assert turned main
    red on every release whether or not anything the pin protects had moved. What the pin
    protects is the overhead constant and the tool list, and the two tests below measure
    those on the installed CLI. A version change alone is a warning: re-pin when convenient."""
    installed = cli_calibration.installed_cli_version()
    if installed != charging.CALIBRATED_CLI_VERSION:
        warnings.warn(
            f"installed claude CLI is {installed}, the overhead constant was measured on "
            f"{charging.CALIBRATED_CLI_VERSION}: re-run `python3 harness/cli_calibration.py`, "
            f"update charging.LOCAL_CLI_OVERHEAD_TOKENS and CALIBRATED_CLI_VERSION")
    assert installed, "claude --version gave nothing"


def _long_prompt():
    """Long enough that the CLI would fire its session-title side call if it could."""
    return cli_calibration.representative_prompt()


@pytest.fixture(scope="module")
def probe():
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not installed")
    prompt = _long_prompt()
    return prompt, cli_calibration.measure(True, prompt=prompt)


@needs_cli
def test_calibrated_overhead_constant_matches_a_fresh_measurement():
    fresh = cli_calibration.measure(True)["overhead_tokens"]
    assert fresh == pytest.approx(charging.LOCAL_CLI_OVERHEAD_TOKENS, rel=0.03)


def test_a_local_worker_occupies_exactly_one_slot_and_is_under_target(probe):
    prompt, r = probe
    assert r["exit_code"] == 0
    assert len(r["requests"]) == 1, r["requests"]  # no side request on the Mac
    assert sorted(r["requests"][0]["tools"]) == sorted(fworker.LOCAL_CLI_TOOLS.split(","))
    assert r["first_request_tokens"] <= 6000
    # the size gate's estimate covers the measured request
    assert charging.prompt_tokens_est(prompt, True) >= r["first_request_tokens"] * 0.97


# ---------- honest size gate ----------

def test_size_gate_adds_the_cli_overhead_only_under_the_profile():
    text = "x" * 3500
    assert charging.prompt_tokens_est(text) == charging.estimate_tokens(text)
    assert (charging.prompt_tokens_est(text, True)
            == charging.estimate_tokens(text) + charging.LOCAL_CLI_OVERHEAD_TOKENS)
    env = {"FOREMAN_SERVED_CTX": "4096"}  # budget 2457 < overhead
    assert charging.prompt_over_ctx("x", env) is None
    assert "CLI overhead" in charging.prompt_over_ctx("x", env, cli_profile=True)


# ---------- first-frame budget gate ----------

def test_budget_gate_uses_prefill_rate_doubled_for_a_shared_box():
    env = {"FOREMAN_PREFILL_TPS": "13.6"}
    assert charging.prompt_over_budget(10_000, 1500, env) is None       # 10000/13.6*2 = 1470s
    over = charging.prompt_over_budget(10_400, 1500, env)               # 1529s
    assert over and "13.6" in over and "1500s" in over
    slow = {"FOREMAN_PREFILL_TPS": "7.4"}                               # nephthys
    assert charging.prompt_over_budget(8_800, 2400, slow) is None
    assert charging.prompt_over_budget(9_000, 2400, slow)


@pytest.mark.parametrize("env,budget", [({}, 1500), ({"FOREMAN_PREFILL_TPS": "junk"}, 1500),
                                        ({"FOREMAN_PREFILL_TPS": "0"}, 1500),
                                        ({"FOREMAN_PREFILL_TPS": "1"}, None)])
def test_budget_gate_is_silent_without_a_rate_or_a_budget(env, budget):
    assert charging.prompt_over_budget(10**7, budget, env) is None


def test_a_calibrated_mechanical_edit_prompt_clears_every_configured_backend():
    cfg = json.load(open(os.path.join(os.path.dirname(HARNESS_DIR), "dispatcher",
                                      "config.json")))["local_backends"]
    tokens = charging.prompt_tokens_est(_long_prompt(), True)
    for name, row in cfg.items():
        if isinstance(row, dict) and row.get("prefill_tps"):
            env = {"FOREMAN_PREFILL_TPS": str(row["prefill_tps"])}
            assert charging.prompt_over_budget(tokens, row["first_frame_s"], env) is None, name


def test_over_budget_spawn_is_refused_uncharged_without_a_cooldown(tmp_path):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-" + "test-foreman-key")
    cooldown = tmp_path / "backend_cooldown.json"
    env, _db, _events, runs_root, _wt, _state = make_scratch_env(
        tmp_path, 0, "success", extra={
            "FOREMAN_LITELLM_KEY_FILE": str(key_file), "FOREMAN_FIRST_FRAME_S": "60",
            "FOREMAN_PREFILL_TPS": "13.6", "FOREMAN_BACKEND": "hathor",
            "FOREMAN_BACKEND_COOLDOWN_FILE": str(cooldown)})
    ticket_id = wq_add(env, "scratch over-budget ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, "success", model=LOCAL, effort="",
                         archetype="mechanical-edit")
    assert result.returncode == fworker.EXIT_LAUNCH_REFUSED, result.stdout + result.stderr
    assert "PROMPT_OVER_BUDGET" in result.stdout + result.stderr
    run = os.path.join(runs_root, f"wq{ticket_id}")
    assert not os.path.exists(os.path.join(run, "attempt-1.json"))  # uncharged
    assert not cooldown.exists()
    assert wq_status(env, ticket_id) == "open"


def test_legacy_mode_never_refuses_on_budget(tmp_path):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-" + "test-foreman-key")
    env, *_ = make_scratch_env(tmp_path, 0, "success", extra={
        "FOREMAN_LITELLM_KEY_FILE": str(key_file), "FOREMAN_FIRST_FRAME_S": "60",
        "FOREMAN_PREFILL_TPS": "13.6", "FOREMAN_CHARGE_MODE": "legacy"})
    ticket_id = wq_add(env, "scratch legacy over-budget ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, "success", model=LOCAL, effort="",
                         archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr


# ---------- charging nuance ----------

def _kill(**kw):
    base = dict(turns=0, is_local=True, killed_reason="first_frame", first_frame_s=1500)
    return charging.classify_charge("HANG", **dict(base, **kw))


def test_first_frame_kill_of_an_oversized_prompt_is_the_harness_not_the_rung():
    # 21,000 tokens at 13.6 tok/s = 1544 s > 1500 s: a healthy box could not have made it
    assert _kill(prompt_tokens=21_000, prefill_tps=13.6) == (
        charging.CHARGE_HARNESS, "prompt_over_budget")


def test_first_frame_kill_within_budget_is_still_a_rung_fault():
    assert _kill(prompt_tokens=8_000, prefill_tps=13.6) == (
        charging.CHARGE_RUNG, "first_frame_timeout")
    assert _kill() == (charging.CHARGE_RUNG, "first_frame_timeout")  # no rate recorded


def test_nuance_needs_a_first_frame_kill_on_a_local_rung_with_no_turns():
    big = dict(prompt_tokens=21_000, prefill_tps=13.6)
    assert _kill(killed_reason="inactivity", **big)[0] == charging.CHARGE_RUNG
    assert _kill(turns=3, **big)[0] == charging.CHARGE_TICKET
    assert _kill(is_local=False, **big)[0] == charging.CHARGE_TICKET


def test_nuance_is_off_under_legacy():
    assert _kill(prompt_tokens=21_000, prefill_tps=13.6, env={"FOREMAN_CHARGE_MODE": "legacy"}
                 )[0] == charging.CHARGE_TICKET


def test_only_a_rung_charge_reaches_the_cooldown_writer(tmp_path, monkeypatch):
    """handle_failure cools a backend down only for charged_to == rung, so the nuance's
    harness charge leaves the cooldown file untouched."""
    cooldown = tmp_path / "bc.json"
    monkeypatch.setattr(fworker, "BACKEND_COOLDOWN_FILE", str(cooldown))
    monkeypatch.setattr(fworker, "handle_rung_fault",
                        lambda *a, **k: pytest.fail("cooled the backend"))
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "MAX_ATTEMPTS", 1)  # ladder exhausted: no retry, no wq
    monkeypatch.setattr(fworker, "wq_block", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "wq_release", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "claimed"})
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: type("P", (), {
        "returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: None)
    # wq#2581: this test's model IS a local rung (LOCAL="hathor-llama8b"), so without this
    # it would also exercise escalate_off_local -- which calls router.wq_show (a
    # DIFFERENT function object than fworker.wq_show above, real subprocess unless
    # stubbed) for a ticket id that only exists in this test, raising SystemExit. Not
    # what this test is about (it's the cooldown-file behavior, see docstring); decline
    # escalation so this exercises the same ordinary-block path it always has.
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda *a, **k: (False, "not under test"))
    charged_to, cause = _kill(prompt_tokens=21_000, prefill_tps=13.6)
    assert (charged_to, cause) == (charging.CHARGE_HARNESS, "prompt_over_budget")
    args = type("A", (), {"model": LOCAL, "repo": "/nowhere", "archetype": "mechanical-edit"})()
    try:
        fworker.handle_failure(args, 4101, 1, "HANG", None, [], str(tmp_path),
                               charged_to=charged_to, failure_cause=cause)
    except Exception:
        pass  # what the ticket-side ladder does past this point is not under test
    assert not cooldown.exists()
