"""wq#1116: fworker must claim a ticket under the identity its worker will close it with.

Zero quota: direct import, no `claude -p` and no wq process. The bug this pins is not visible
in either half on its own — build_env() looked fine handing the worker `fworker-wq<id>-a<n>`,
and wq_claim() looked fine claiming with no agent at all. Together they guaranteed that the name
which claimed and the name which closed never matched, on every dispatch, which is what forced
wq's owner check to wave through unregistered callers and let two orchestrator sessions close
each other's tickets on 2026-08-30.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fworker  # noqa: E402


def test_wq_identity_is_per_ticket_not_per_attempt():
    assert fworker.wq_identity(1116) == "fworker-wq1116"
    assert fworker.wq_identity(1116) == fworker.wq_identity("1116")


def test_worker_env_carries_the_claiming_identity():
    """The worker's WQ_AGENT is the holder, not its own per-attempt worker_id."""
    env = fworker.build_env("fworker-wq1116-a3", "claude-sonnet-5", "high", "/tmp/wt", 1116)
    assert env["WQ_AGENT"] == "fworker-wq1116"
    # worker_id still identifies the ATTEMPT for logs, events and worktrees — only the wq
    # identity is per-ticket, and conflating the two is the bug.
    assert env["FOREMAN_WORKER"] == "fworker-wq1116-a3"


def test_retry_attempts_share_one_wq_identity():
    a1 = fworker.build_env("fworker-wq1116-a1", "claude-sonnet-5", "high", "/tmp/wt", 1116)
    a2 = fworker.build_env("fworker-wq1116-a2", "claude-sonnet-5", "high", "/tmp/wt", 1116)
    assert a1["WQ_AGENT"] == a2["WQ_AGENT"]
    assert a1["FOREMAN_WORKER"] != a2["FOREMAN_WORKER"]


def test_claim_names_the_holder(monkeypatch):
    """`wq claim` is passed the agent positionally; with none, wq falls back to lumen:<pid>,
    which is a fresh string every invocation and can never match the closer."""
    seen = []

    class FakeProc:
        returncode = 0
        stdout = "CLAIMED 1116"
        stderr = ""

    monkeypatch.setattr(fworker, "wq", lambda *a, **k: seen.append(a) or FakeProc())
    ok, _ = fworker.wq_claim(1116)
    assert ok
    assert seen == [("claim", "fworker-wq1116", "--id", "1116")]
