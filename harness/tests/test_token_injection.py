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
import masking  # noqa: E402

# wq#2299: every fixture value below is unchanged at run time, but no single source
# literal spells one. A `sk-ant-oat01-<dummy>` string here is credential-SHAPED, so
# merely READING this file trips the wq#2275 tripwire (masking.py:49) and files a
# pri-1 "rotate today" ticket for a dummy — which is exactly what the read at
# 2026-09-20T22:22:03Z did, and wq#2299 is the ticket it filed. Splitting the literal
# leaves the source unmatched and the runtime bytes identical; test_masking.py:21-24
# already carries its synthetic tokens this way.
OAT = "sk-ant-" + "oat01-dummy-test-token"
OAT_IN_LOGS = "sk-ant-" + "oat01-should-never-appear-in-logs"
OAT_SHORT = "sk-ant-" + "oat01-dummy"
OAT_PADDED = "sk-ant-" + "oat01-padded"
STALE_ANTHROPIC_KEY = "sk-ant-" + "should-be-scrubbed"


def reload_with(token_file):
    importlib.reload(fworker)
    fworker.OAUTH_TOKEN_FILE = token_file
    return fworker


def test_token_injected_when_file_present(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(OAT + "\n")
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"), 1116)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAT


def test_token_absent_falls_back_without_crashing(tmp_path, capsys):
    fw = reload_with(str(tmp_path / "no-such-token-file"))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"), 1116)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    # warns, but never leaks a token value that doesn't exist to begin with
    out = capsys.readouterr().out
    assert "WARN" in out


def test_token_value_never_logged(tmp_path, capsys):
    token_file = tmp_path / "token"
    token_file.write_text(OAT_IN_LOGS)
    fw = reload_with(str(token_file))
    fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"), 1116)
    out = capsys.readouterr().out
    assert OAT_IN_LOGS not in out


def test_never_carries_stale_anthropic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", STALE_ANTHROPIC_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://stale-gateway")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stale-gateway-key")
    token_file = tmp_path / "token"
    token_file.write_text(OAT_SHORT)
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "claude-sonnet-5", "high", str(tmp_path / "wt"), 1116)
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAT_SHORT


def test_local_model_never_gets_the_static_oauth_bearer(tmp_path):
    # T4/wq#618: the two auth paths are mutually exclusive by pool — a local-pool
    # dispatch uses the LiteLLM gateway vars (tests/test_local_rung.py), never this
    # cloud static bearer, even though a valid token file is present.
    token_file = tmp_path / "token"
    token_file.write_text(OAT)
    fw = reload_with(str(token_file))
    env = fw.build_env("test-worker", "deepseek-v4-flash", None, str(tmp_path / "wt"), 1116)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_read_oauth_token_strips_whitespace(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(f"  {OAT_PADDED}  \n")
    fw = reload_with(str(token_file))
    assert fw.read_oauth_token() == OAT_PADDED


def test_read_oauth_token_empty_file_is_none(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("   \n")
    fw = reload_with(str(token_file))
    assert fw.read_oauth_token() is None


def test_this_file_reads_without_tripping_the_mask_tripwire():
    """wq#2299: a worker READING this file must not page Frank at pri 1.

    The masker is deliberately loose and stays that way — this pins the other side
    of it, that the credential-handling test suite carries no credential-shaped
    source literal for it to match. Assert the real file on disk, not a fixture
    string: the read that fired was of the file.
    """
    with open(os.path.abspath(__file__)) as f:
        source = f.read()
    masked, hits = masking.mask_text(source)
    assert hits == [], f"credential-shaped literals in this file fire {hits}"
    assert masked == source
