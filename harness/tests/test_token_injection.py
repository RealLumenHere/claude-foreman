"""wq#616 (T2b) static oauth-token injection into cloud-worker env — BUILD_PLAN.md §T2b
"Cloud workers use CLAUDE_CODE_OAUTH_TOKEN=<setup-token> static bearer". Zero quota,
direct import of build_env() — no `claude -p` invoked.
"""
import importlib
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def reload_with(token_file):
    importlib.reload(fworker)
    fworker.OAUTH_TOKEN_FILE = token_file
    return fworker


def test_token_injected_when_file_present(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("sk-ant-oat01-dummy-test-token\n")
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-dummy-test-token"


def test_token_absent_falls_back_without_crashing(tmp_path, capsys):
    fw = reload_with(str(tmp_path / "no-such-token-file"))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    # warns, but never leaks a token value that doesn't exist to begin with
    out = capsys.readouterr().out
    assert "WARN" in out


def test_token_value_never_logged(tmp_path, capsys):
    token_file = tmp_path / "token"
    secret = "sk-ant-oat01-should-never-appear-in-logs"
    token_file.write_text(secret)
    fw = reload_with(str(token_file))
    fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"))
    out = capsys.readouterr().out
    assert secret not in out


def test_never_carries_stale_anthropic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-scrubbed")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://stale-gateway")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stale-gateway-key")
    token_file = tmp_path / "token"
    token_file.write_text("sk-ant-oat01-dummy")
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"))
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-dummy"


def test_local_model_never_gets_the_static_oauth_bearer(tmp_path):
    # T4/wq#618: the two auth paths are mutually exclusive by pool — a local-pool
    # dispatch uses the LiteLLM gateway vars (tests/test_local_rung.py), never this
    # cloud static bearer, even though a valid token file is present.
    token_file = tmp_path / "token"
    token_file.write_text("sk-ant-oat01-dummy-test-token")
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "deepseek-v4-flash", None, str(tmp_path / "wt"))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_read_oauth_token_strips_whitespace(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  sk-ant-oat01-padded  \n")
    fw = reload_with(str(token_file))
    assert fw.read_oauth_token() == "sk-ant-oat01-padded"


def test_read_oauth_token_empty_file_is_none(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("   \n")
    fw = reload_with(str(token_file))
    assert fw.read_oauth_token() is None
