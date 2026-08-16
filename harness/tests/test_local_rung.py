"""T4 local rung (BUILD_PLAN.md §T4, wq#618): unit + end-to-end coverage for the
LiteLLM gateway env wiring, the mechanical-edit/probe-build archetype restriction,
the lowered local --max-turns, and the mandatory Sonnet-5 verify-pass gate on a
local-authored ticket. Zero Anthropic quota (fake_claude shim, scratch wq DB).
"""
import json
import os
import re
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

from test_fworker_integration import (  # noqa: E402 — reuse the scratch-fixture helpers
    make_scratch_repo, make_scratch_env, wq_add, wq_status, run_fworker, WQ_BIN,
)

FAKE_CLAUDE = os.path.join(TESTS_DIR, "fake_claude.py")


# ---------- is_local_model / local_rung_gate (pure unit) ----------

def test_is_local_model_matches_trusted_routes_only():
    assert fworker.is_local_model("deepseek-v4-flash") is True
    assert fworker.is_local_model("qwen36-local") is True
    assert fworker.is_local_model("nephthys-qwen3-14b") is False, \
        "fallback brain only, not a wq worker (models.yaml)"
    assert fworker.is_local_model("claude-sonnet-5") is False
    assert fworker.is_local_model(None) is False


def test_local_rung_gate_allows_the_two_trusted_archetypes():
    for arch in ("mechanical-edit", "probe-build"):
        allow, reason = fworker.local_rung_gate("deepseek-v4-flash", arch)
        assert allow is True and reason is None


def test_local_rung_gate_denies_every_other_archetype():
    for arch in ("standard-engineering", "rca-investigation", "verify-pass",
                 "novel-design", "research-synthesis", "long-horizon-campaign"):
        allow, reason = fworker.local_rung_gate("deepseek-v4-flash", arch)
        assert allow is False
        assert "not trusted" in reason


def test_local_rung_gate_is_a_noop_for_cloud_models():
    allow, reason = fworker.local_rung_gate("claude-sonnet-5", "rca-investigation")
    assert allow is True and reason is None


# ---------- build_env: gateway wiring, never global, never leaks to cloud ----------

def test_build_env_wires_gateway_vars_for_local_model(tmp_path, monkeypatch):
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-test-foreman-key")
    monkeypatch.setattr(fworker, "LITELLM_KEY_FILE", str(key_file))

    env = fworker.build_env("wid", "deepseek-v4-flash", "medium", "/tmp/wt")

    assert env["ANTHROPIC_BASE_URL"] == fworker.LITELLM_BASE_URL
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-foreman-key"
    assert env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "1"
    assert "ANTHROPIC_API_KEY" not in env


def test_build_env_never_sets_gateway_vars_for_a_cloud_model():
    env = fworker.build_env("wid", "claude-sonnet-5", "high", "/tmp/wt")
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_build_env_strips_inherited_gateway_vars_for_a_cloud_model(monkeypatch):
    # a prior local-rung attempt's vars must never leak into a later cloud dispatch
    # sharing the same host/session (BUILD_PLAN.md §T4: "never global").
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leaked-litellm-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked-api-key")
    monkeypatch.setenv("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "1")

    env = fworker.build_env("wid", "claude-sonnet-5", "high", "/tmp/wt")

    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" not in env


# ---------- lowered local --max-turns ----------

def test_local_max_turns_lower_than_cloud_defaults_for_trusted_archetypes():
    for arch in ("mechanical-edit", "probe-build"):
        assert fworker.LOCAL_ARCHETYPE_MAX_TURNS[arch] < fworker.ARCHETYPE_MAX_TURNS[arch]


# ---------- end-to-end: dispatch, gateway env reaches the child, verify-pass gate ----------

def test_local_rung_end_to_end_wires_env_and_files_sonnet_verify_pass(tmp_path):
    repo = make_scratch_repo(tmp_path)
    key_file = tmp_path / "litellm.token"
    key_file.write_text("sk-test-foreman-key")
    env_dump = tmp_path / "child_env.json"
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "success",
        extra={
            "FOREMAN_LITELLM_KEY_FILE": str(key_file),
            "FAKE_CLAUDE_ENV_DUMP": str(env_dump),
        })
    ticket_id = wq_add(env, "scratch local-rung mechanical-edit ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success", model="deepseek-v4-flash",
                          effort="", archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    # the child (fake_claude, standing in for `claude -p`) actually received the
    # gateway env — not just build_env()'s return value in isolation.
    dumped = json.loads(env_dump.read_text())
    assert dumped.get("ANTHROPIC_BASE_URL") == "http://localhost:4000"
    assert dumped.get("ANTHROPIC_AUTH_TOKEN") == "sk-test-foreman-key"
    assert dumped.get("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY") == "1"
    assert "ANTHROPIC_API_KEY" not in dumped

    # mandatory Sonnet-5 verify-pass gate (models.yaml verify_pass.local_author_always,
    # orchestrator/judgment.py): a [verify] ticket must exist for this shipped ticket,
    # authored sonnet-5@high regardless of the local model that shipped the diff.
    listing = subprocess.run([WQ_BIN, "list"], capture_output=True, text=True, env=env)
    verify_line = next((l for l in listing.stdout.splitlines()
                         if f"[verify] wq#{ticket_id}" in l), None)
    assert verify_line, f"no [verify] ticket filed:\n{listing.stdout}"
    verify_id = int(re.match(r"\s*\[\s*(\d+)\]", verify_line).group(1))

    shown = subprocess.run([WQ_BIN, "show", str(verify_id)], capture_output=True,
                            text=True, env=env)
    cols = dict(l.split(":", 1) for l in shown.stdout.splitlines() if ":" in l)
    cols = {k.strip(): v.strip() for k, v in cols.items()}
    assert cols["model"] == "claude-sonnet-5"
    assert cols["effort"] == "high"
    assert cols["archetype"] == "verify-pass"
    assert cols["deps"] == str(ticket_id)


def test_local_rung_gate_defers_before_claim_on_disallowed_archetype(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    ticket_id = wq_add(env, "scratch local-rung misrouted ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success", model="deepseek-v4-flash",
                          effort="", archetype="rca-investigation")
    assert result.returncode == fworker.EXIT_DEFERRED, result.stdout + result.stderr
    assert "DEFERRED" in result.stdout

    # never claimed, never spawned — same contract as deps_unmet/governor-deny.
    assert wq_status(env, ticket_id) == "open"
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))
