"""wq#2128 (dispatch rebuild step 4): first-frame timeout split, cause-typed charging,
rung-fault release, backend cooldown file, slim local prompt and its size gate.
Rule table under test: research/DISPATCH_FIX_RESEARCH.md section 2.2.
"""
import json
import os
import sys
import textwrap
import time

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import charging  # noqa: E402
import fworker  # noqa: E402

from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, run_fworker,
)

BACKEND_5XX = [{"type": "system", "subtype": "api_retry", "error_status": 503,
                "error": "server_error"}]
BACKEND_DOWN = [{"type": "unparsed", "raw": "Error: connect ECONNREFUSED 100.1.2.3:4000"}]
CTX_OVERFLOW = [{"type": "result", "subtype": "error_during_execution",
                 "result": "litellm.ContextWindowExceededError: prompt is too long"}]


# ---------- timeout split (run_child) ----------

def _child(tmp_path, body):
    script = tmp_path / "child.py"
    script.write_text("import json, time\n"
                      "def emit(f):\n    print(json.dumps(f), flush=True)\n"
                      + textwrap.dedent(body))
    return [sys.executable, str(script)]


def _run(cmd, tmp_path, **kw):
    return fworker.run_child(cmd, str(tmp_path), dict(os.environ), max_turns=50,
                             wall_clock_s=30, ticket_id=999901, attempt=1,
                             post_result_grace_s=1, **kw)


INIT = 'emit({"type": "system", "subtype": "init", "session_id": "s", "model": "m"})\n'
ASSISTANT = ('emit({"type": "assistant", "message": {"content": '
             '[{"type": "text", "text": "hi"}]}})\n')
RESULT = 'emit({"type": "result", "subtype": "success", "is_error": False})\n'


def test_first_frame_window_ignores_init_and_kills_on_first_frame_timeout(tmp_path):
    cmd = _child(tmp_path, INIT + "time.sleep(60)\n")
    t0 = time.time()
    state, exit_code, events, killed, _cpu, _pid = _run(cmd, tmp_path, frame_gap_s=30,
                                                        first_frame_s=1)
    assert killed == "first_frame"
    assert time.time() - t0 < 10
    assert state.hung is True and state.first_frame_timeout is True
    assert state.turns == 0 and state.saw_init


def test_inactivity_only_applies_after_the_first_model_frame(tmp_path):
    cmd = _child(tmp_path, INIT + ASSISTANT + "time.sleep(60)\n")
    state, _exit, _events, killed, _cpu, _pid = _run(cmd, tmp_path, frame_gap_s=1,
                                                     first_frame_s=30)
    assert killed == "inactivity"
    assert state.hung is True and state.first_frame_timeout is False
    assert state.turns == 1


def test_slow_first_frame_is_not_an_inactivity_hang(tmp_path):
    """init lands at once, the first model frame 2.5s later: with frame_gap 1s this would
    be a HANG under the old single window, and is the whole local prompt-eval problem."""
    cmd = _child(tmp_path, INIT + "time.sleep(2.5)\n" + ASSISTANT + RESULT)
    _state, exit_code, _events, killed, _cpu, _pid = _run(cmd, tmp_path, frame_gap_s=1,
                                                          first_frame_s=20)
    assert killed is None and exit_code == 0


def test_no_first_frame_budget_keeps_the_single_window(tmp_path):
    cmd = _child(tmp_path, INIT + "time.sleep(60)\n")
    _state, _exit, _events, killed, _cpu, _pid = _run(cmd, tmp_path, frame_gap_s=1)
    assert killed == "inactivity"


def test_first_frame_timeout_values():
    assert charging.first_frame_timeout_s(False, env={"FOREMAN_FIRST_FRAME_S": "9"}) == 240
    assert charging.first_frame_timeout_s(True, env={}) == 1500
    assert charging.first_frame_timeout_s(True, env={"FOREMAN_FIRST_FRAME_S": "600"}) == 600
    assert charging.first_frame_timeout_s(True, env={"FOREMAN_FIRST_FRAME_S": "junk"}) == 1500
    assert fworker.first_frame_timeout("claude-sonnet-5") == 240
    assert fworker.first_frame_timeout("deepseek-v4-flash") == 240


def test_is_local_rung_excludes_deepseek_and_includes_the_worker_group():
    assert fworker.is_local_rung("hathor-llama8b")
    assert fworker.is_local_rung("local-worker")
    assert not fworker.is_local_rung("deepseek-v4-flash")
    assert not fworker.is_local_rung("claude-sonnet-5")


# ---------- charging table (FIX_RESEARCH 2.2), one test per row ----------

def _charge(cls, **kw):
    kw.setdefault("env", {})
    return charging.classify_charge(cls, **kw)


def test_row_local_hang_without_turns_is_a_rung_fault():
    assert _charge("HANG", turns=0, is_local=True, killed_reason="first_frame") == (
        "rung", "first_frame_timeout")
    assert _charge("HANG", turns=0, is_local=True, killed_reason="inactivity") == (
        "rung", "hang_no_turns")


def test_row_litellm_5xx_timeout_cooldown_is_a_rung_fault():
    assert _charge("CRASH", turns=3, is_local=True, events=BACKEND_5XX) == (
        "rung", "backend_5xx")
    assert _charge("CRASH", turns=0, is_local=True, result_text=
                   "litellm.APITimeoutError: Request timed out") == (
        "rung", "backend_unreachable")
    assert _charge("INDETERMINATE", is_local=True, result_text=
                   "No healthy deployments: all in cooldown")[0] == "rung"


def test_row_backend_unreachable_is_a_rung_fault():
    assert _charge("LAUNCH_FAILURE", is_local=True, events=BACKEND_DOWN) == (
        "rung", "backend_unreachable")


def test_row_ctx_overflow_is_a_rung_fault():
    assert _charge("CRASH", turns=1, is_local=True, events=CTX_OVERFLOW) == (
        "rung", "ctx_overflow")


def test_row_launch_failure_is_the_harness():
    assert _charge("LAUNCH_FAILURE", is_local=True) == ("harness", "launch_failure")
    assert _charge("LAUNCH_FAILURE", is_local=False, events=BACKEND_DOWN)[0] == "harness"
    assert _charge("MODEL_NOT_FOUND")[0] == "harness"


def test_row_limit_and_budget_are_the_budget():
    assert _charge("LIMIT")[0] == "budget"
    assert _charge("BUDGET")[0] == "budget"
    assert _charge("LIMIT", is_local=True, events=BACKEND_5XX)[0] == "budget"


@pytest.mark.parametrize("cls,kw,cause", [
    ("TURNS", {}, "turns"),
    ("FALSE_COMPLETE", {}, "false_complete"),
    ("PERMISSION_LOOP", {}, "permission_loop"),
    ("CRASH", {"turns": 4}, "crash_after_turns"),
    ("HANG", {"turns": 4, "is_local": True}, "hang_after_turns"),
])
def test_row_ticket_charged(cls, kw, cause):
    assert _charge(cls, **kw) == ("ticket", cause)


def test_hang_after_turns_stays_ticket_even_with_backend_noise():
    assert _charge("HANG", turns=5, is_local=True, events=BACKEND_5XX)[0] == "ticket"


def test_cloud_hang_without_turns_is_still_the_ticket():
    assert _charge("HANG", turns=0, is_local=False, killed_reason="first_frame") == (
        "ticket", "hang_no_turns")
    assert _charge("CRASH", turns=2, is_local=False, events=BACKEND_5XX)[0] == "ticket"


def test_worker_tool_output_is_not_backend_evidence():
    """A worker whose bash ran `curl` that timed out is the ticket's failure: only api
    frames, unparsed stderr and the result text are scanned, never tool_result content."""
    ev = [{"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "curl: ECONNREFUSED"}]}}]
    assert _charge("CRASH", turns=3, is_local=True, events=ev)[0] == "ticket"


def test_legacy_mode_restores_todays_charging():
    legacy = {"FOREMAN_CHARGE_MODE": "legacy"}
    assert _charge("HANG", turns=0, is_local=True, killed_reason="first_frame",
                   env=legacy)[0] == "ticket"
    assert _charge("CRASH", is_local=True, events=BACKEND_5XX, env=legacy)[0] == "ticket"


def test_success_is_not_a_fault():
    assert _charge("SUCCESS", turns=9, is_local=True) == ("ticket", None)


def test_charged_attempts_skips_only_rung_records():
    """A recorded LAUNCH_FAILURE (harness) must still count, or a broken launch would
    retry forever inside one fworker."""
    recs = [{"charged_to": "rung"}, {"charged_to": "ticket"}, {"charged_to": "harness"},
            {}, {"charged_to": "budget"}]
    assert charging.charged_attempts(recs) == 4
    assert charging.charged_attempts(recs, legacy=True) == 5


# ---------- backend cooldown file ----------

def test_cooldown_starts_at_15_min_doubles_and_caps_at_2h(tmp_path):
    path = str(tmp_path / "rt" / "backend_cooldown.json")
    t = 1_000_000.0
    durations = []
    for i in range(6):
        e = charging.mark_backend_cooldown(path, "hathor", "hang_no_turns", now=t)
        durations.append(e["until"] - t)
        t += 60  # still cooling: the next fault is consecutive
    assert durations == [900, 1800, 3600, 7200, 7200, 7200]


def test_cooldown_restarts_after_a_clean_stretch(tmp_path):
    path = str(tmp_path / "bc.json")
    t = 1_000_000.0
    charging.mark_backend_cooldown(path, "hathor", "x", now=t)
    later = t + 900 + charging.COOLDOWN_MAX_S + 1
    e = charging.mark_backend_cooldown(path, "hathor", "x", now=later)
    assert e["strikes"] == 1 and e["until"] - later == 900


def test_cooldown_file_is_per_backend_atomic_and_readable(tmp_path):
    path = str(tmp_path / "bc.json")
    now = time.time()
    charging.mark_backend_cooldown(path, "hathor", "hang_no_turns", now=now)
    charging.mark_backend_cooldown(path, "nephthys", "backend_5xx", now=now)
    doc = json.load(open(path))
    assert set(doc) == {"hathor", "nephthys"} and doc["nephthys"]["cause"] == "backend_5xx"
    assert charging.backend_cooling(path, "hathor", now=now + 10)
    assert not charging.backend_cooling(path, "hathor", now=now + 901)
    assert not charging.backend_cooling(path, "isis", now=now)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_cooldown_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "bc.json"
    path.write_text("{not json")
    e = charging.mark_backend_cooldown(str(path), "hathor", "x")
    assert e["strikes"] == 1 and "hathor" in json.loads(path.read_text())


# ---------- rung-fault release (handle_failure) ----------

@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    calls = {"release": [], "block": [], "note": [], "ntfy": []}
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(fworker, "BACKEND_COOLDOWN_FILE", str(tmp_path / "bc.json"))
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "check_artifacts_done", lambda *a, **k: (False, None))
    monkeypatch.setattr(fworker, "wq_release", lambda t: calls["release"].append(t))
    monkeypatch.setattr(fworker, "wq_block", lambda t, r: calls["block"].append((t, r)))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: calls["ntfy"].append(a))

    class _P:
        returncode = 0
        stdout = stderr = ""
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: (calls["note"].append(a), _P())[1])
    monkeypatch.setattr(fworker, "do_attempt",
                        lambda *a, **k: pytest.fail("a rung fault must not retry"))
    return calls


class _Args:
    model = "hathr-not-real"
    repo = "/nowhere"
    archetype = "mechanical-edit"


def _write_attempt(ticket, n, charged_to):
    d = fworker.run_dir(ticket)
    with open(os.path.join(d, f"attempt-{n}.json"), "w") as f:
        json.dump({"attempt": n, "charged_to": charged_to}, f)


def test_rung_fault_releases_records_cooldown_and_skips_the_retry_ladder(stubbed, tmp_path,
                                                                        monkeypatch):
    monkeypatch.setenv("FOREMAN_BACKEND", "hathor")
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "claimed"})
    args = _Args()
    args.model = "hathor-llama8b"
    rc = fworker.handle_failure(args, 4001, 1, "HANG", None, [], str(tmp_path),
                                charged_to="rung", failure_cause="first_frame_timeout")
    assert rc == fworker.EXIT_RUNG_FAULT
    assert stubbed["release"] == [4001] and not stubbed["block"]
    assert any("RUNG_FAULT:" in str(a) for a in stubbed["note"])
    assert charging.backend_cooling(str(tmp_path / "bc.json"), "hathor")


def test_rung_fault_that_ends_blocked_carries_the_prefix(stubbed, tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {
        "status": "blocked", "block_reason": "worker gave up"})
    args = _Args()
    args.model = "hathor-llama8b"
    fworker.handle_failure(args, 4002, 1, "CRASH", None, [], str(tmp_path),
                           charged_to="rung", failure_cause="backend_5xx")
    assert stubbed["block"] == [(4002, "RUNG_FAULT: worker gave up")]
    assert not stubbed["release"]


def test_rung_faults_do_not_count_toward_max_attempts(stubbed, monkeypatch):
    _write_attempt(4003, 1, "rung")
    _write_attempt(4003, 2, "rung")
    _write_attempt(4003, 3, "ticket")
    assert fworker.charged_attempt_count(4003, 3) == 1 < fworker.MAX_ATTEMPTS
    monkeypatch.setenv("FOREMAN_CHARGE_MODE", "legacy")
    assert fworker.charged_attempt_count(4003, 3) == 3


def test_ticket_charged_failure_still_retries_within_max_attempts(stubbed, monkeypatch,
                                                                   tmp_path):
    _write_attempt(4004, 1, "ticket")
    seen = []
    monkeypatch.setattr(fworker, "do_attempt", lambda a, n, **k: seen.append(n) or 0)
    fworker.handle_failure(_Args(), 4004, 1, "TURNS", None, [], str(tmp_path),
                           charged_to="ticket", failure_cause="turns")
    assert seen == [2]


# ---------- slim local prompt + size gate ----------

TICKET_A = {"title": "fix typo in README", "detail": "Change 'teh' to 'the' in README.md."}
TICKET_B = {"title": "bump a constant", "detail": "Set FOO = 2 in config.py {{TITLE}}"}


def _render(archetype, ticket_id, ticket, notes=None, acceptance=None, rework=None,
            worktree="/wt/x"):
    tmpl = fworker.local_prompt_template("hathor-llama8b", archetype)
    assert tmpl
    return fworker.load_local_prompt(tmpl, ticket_id, ticket, worktree, notes,
                                     acceptance_doc=acceptance, rework=rework)


@pytest.mark.parametrize("archetype", ["mechanical-edit", "probe-build"])
def test_local_prompt_static_prefix_is_byte_identical_across_tickets(archetype):
    a = _render(archetype, 1, TICKET_A)
    b = _render(archetype, 2, TICKET_B, worktree="/wt/other")
    prefix_a, sep, _ = a.partition("\n---\n")
    prefix_b, _, _ = b.partition("\n---\n")
    assert sep and prefix_a == prefix_b
    assert "{{" not in prefix_a
    assert a.startswith(prefix_a) and b.startswith(prefix_a)


@pytest.mark.parametrize("archetype", ["mechanical-edit", "probe-build"])
def test_local_prompt_is_slim_and_fits_the_default_ctx_gate(archetype):
    text = _render(archetype, 1, TICKET_A)
    assert charging.estimate_tokens(text) <= 4000
    assert charging.prompt_over_ctx(text, env={}) is None
    assert "INSTINCTS" not in text and "DISTILLED" not in text


def test_local_prompt_fills_the_ticket_and_never_re_expands_placeholders():
    text = _render("mechanical-edit", 7, TICKET_B, notes=None)
    tail = text.partition("\n---\n")[2]
    assert "YOUR TICKET: wq#7: bump a constant" in tail
    assert "Set FOO = 2 in config.py {{TITLE}}" in tail
    assert "WORKTREE: /wt/x" in tail
    assert "{{REWORK}}" not in tail and "{{ACCEPTANCE_CONTRACT}}" not in tail


def test_local_prompt_template_selection_rules(monkeypatch):
    assert fworker.local_prompt_template("hathor-llama8b", "mechanical-edit")
    assert fworker.local_prompt_template("local-worker", "probe-build")
    assert fworker.local_prompt_template("claude-sonnet-5", "mechanical-edit") is None
    assert fworker.local_prompt_template("deepseek-v4-flash", "mechanical-edit") is None
    assert fworker.local_prompt_template("hathor-llama8b", "standard-engineering") is None
    monkeypatch.setenv("FOREMAN_CHARGE_MODE", "legacy")
    assert fworker.local_prompt_template("hathor-llama8b", "mechanical-edit") is None


def test_prompt_size_gate_uses_chars_over_3_5_and_0_6_of_served_ctx():
    env = {"FOREMAN_SERVED_CTX": "4096"}
    assert charging.prompt_budget_tokens(env) == 2457
    assert charging.prompt_budget_tokens({}) == int(0.6 * 32768)
    assert charging.estimate_tokens("x" * 3500) == 1001
    assert charging.prompt_over_ctx("x" * int(2457 * 3.5 - 10), env) is None
    over = charging.prompt_over_ctx("x" * int(2457 * 3.5 + 100), env)
    assert over and "2457" in over


# ---------- end to end: local hang -> rung fault, ticket released ----------

def test_local_first_frame_hang_releases_ticket_uncharged_and_cools_the_backend(tmp_path):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-test-foreman-key")
    cooldown = tmp_path / "backend_cooldown.json"
    env, _db, _events, runs_root, _wt, _state = make_scratch_env(
        tmp_path, 0, "hang", extra={
            "FOREMAN_LITELLM_KEY_FILE": str(key_file),
            "FOREMAN_FIRST_FRAME_S": "2",
            "FOREMAN_BACKEND": "hathor",
            "FOREMAN_BACKEND_COOLDOWN_FILE": str(cooldown),
        })
    ticket_id = wq_add(env, "scratch local hang ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "hang", model="hathor-llama8b",
                         effort="", archetype="mechanical-edit")
    assert result.returncode == fworker.EXIT_RUNG_FAULT, result.stdout + result.stderr

    rec = json.load(open(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")))
    assert rec["killed_reason"] == "first_frame" and rec["turns"] == 0
    assert rec["classification"] == "HANG"
    assert rec["charged_to"] == "rung" and rec["failure_cause"] == "first_frame_timeout"
    assert rec["first_frame_s"] == 2
    assert rec["prompt_profile"] == "local_mechanical-edit.md"
    assert not os.path.exists(os.path.join(runs_root, f"wq{ticket_id}", "attempt-2.json"))
    assert wq_status(env, ticket_id) == "open"
    assert json.load(open(cooldown))["hathor"]["strikes"] == 1


def test_cloud_hang_is_charged_to_the_ticket_and_records_the_fields(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, _db, _events, runs_root, _wt, _state = make_scratch_env(tmp_path, 0, "hang")
    ticket_id = wq_add(env, "scratch cloud hang ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    run_fworker(env, ticket_id, repo, "hang",
                extra_args=["--frame-gap-timeout", "1", "--post-result-grace", "1"])
    rec = json.load(open(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")))
    assert rec["killed_reason"] == "inactivity"
    assert rec["charged_to"] == "ticket" and rec["failure_cause"] == "hang_no_turns"
