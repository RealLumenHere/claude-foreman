"""wq#954 — the Bash output cap rides on BASH_MAX_OUTPUT_LENGTH, not guard.sh.

wq#729 capped Bash by rewriting the command to `{ cmd } 2>&1 | head -c 2000`.
That cap lived in the pipeline, so every shell-state verb (`cd`, `export`,
`source`, ...) had to be exempted — piping subshells the left side and would
silently eat the state change. Those exemptions were the residual leak: 168
results, 725,170 B, 20.1% of the corpus's over-cap bytes (research/UNCAPPED_BASH.md
§4). claude-code 2.1.233 truncates the assembled result natively, so the cap moved
to the worker env and no command shape can opt out.

Zero quota: direct import of build_env(), no `claude -p` invoked. The companion
absence-test lives in test_guard.py; the end-to-end "the child really got it"
assertion lives in test_local_rung.py's env-dump test.
"""
import importlib
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

# From the 2.1.233 binary (research/UNCAPPED_BASH.md §4): unset or <= 0 falls back
# to 30000, anything above 150000 is clamped down to it. There is no lower bound.
CLI_UPPER_LIMIT = 150000


def test_build_env_sets_the_native_cap():
    env = fworker.build_env("wid", "claude-sonnet-5", "high", "/tmp/wt", 1116)
    assert env["BASH_MAX_OUTPUT_LENGTH"] == "2000"


def test_cap_applies_to_local_models_too():
    env = fworker.build_env("wid", "deepseek-v4-flash", "medium", "/tmp/wt", 1116)
    assert env["BASH_MAX_OUTPUT_LENGTH"] == "2000"


def test_cap_is_env_overridable(monkeypatch):
    monkeypatch.setenv("FOREMAN_BASH_OUTPUT_CAP_CHARS", "500")
    try:
        fw = importlib.reload(fworker)
        env = fw.build_env("wid", "claude-sonnet-5", "high", "/tmp/wt", 1116)
        assert env["BASH_MAX_OUTPUT_LENGTH"] == "500"
    finally:
        monkeypatch.undo()
        importlib.reload(fworker)


def test_cap_is_a_value_the_cli_will_actually_honour():
    # A cap of 0, a negative, or anything unparseable silently reverts the worker
    # to the 30000 default — a 15x regression that no other test would catch.
    n = int(fworker.BASH_MAX_OUTPUT_LENGTH)
    assert 0 < n <= CLI_UPPER_LIMIT
