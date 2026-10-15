"""wq#3966 -- tests for the adversarial critic pass in the Limpet analysis loop.

Covers the ticket's acceptance set: a finding lacking evidence (deterministic
gate rejects, no model call needed), a supported finding (accepted), and a
contested one (critic disagrees -> a ledger obligation is opened instead of
the finding being silently dropped). Also covers the other half of the
deterministic check (a finding may not close an open obligation as a side
effect) and the CLI wiring.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import critic  # noqa: E402
import obligations as ob  # noqa: E402
import workspace_cli as wc  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "evidence" / "strings").mkdir(parents=True)
    (w / "evidence" / "strings" / "hits.txt").write_text("partition2.signed=0\n")
    (w / "manifest.json").write_text(json.dumps({"target_id": "acme-router-v2", "class": "firmware"}))
    _git("-C", str(w), "init", "-q")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "commit", "-q", "-m", "init")
    return str(w)


SUPPORTED_FINDING = {
    "id": "F-001", "title": "Partition 2 is unsigned", "severity": "high",
    "summary": "strings scan shows partition2.signed=0",
    "evidence": [{"path": "evidence/strings/hits.txt", "locator": "L1"}],
}

UNSUPPORTED_FINDING = {
    "id": "F-002", "title": "Bootloader is unlocked", "severity": "critical",
    "summary": "unverified claim, nothing scanned yet",
}


def test_finding_without_evidence_rejected_before_any_model_call(ws):
    result = critic.review(ws, UNSUPPORTED_FINDING, critic_verdict="supported",
                           critic_rationale="would never reach this")
    assert result["verdict"] == critic.VERDICT_REJECTED
    assert "cites no evidence entry" in result["reasons"][0]
    assert result["obligation"] is None
    assert ob.list_obligations(ws) == []  # the ledger is untouched


def test_supported_finding_accepted_with_no_ledger_change(ws):
    result = critic.review(ws, SUPPORTED_FINDING, critic_verdict="supported",
                           critic_rationale="matches the strings-scan evidence")
    assert result == {"verdict": critic.VERDICT_ACCEPTED, "reasons": [], "obligation": None}
    assert ob.list_obligations(ws) == []


def test_contested_finding_opens_ledger_obligation_instead_of_dropping(ws):
    result = critic.review(ws, SUPPORTED_FINDING, critic_verdict="falsified",
                           critic_rationale="the signed flag applies to partition 1, not 2")
    assert result["verdict"] == critic.VERDICT_CONTESTED
    ob_rec = result["obligation"]
    assert ob_rec is not None and ob_rec["status"] == "open"
    assert "F-001" in ob_rec["claim"] and "partition 1" in ob_rec["claim"]
    assert ob_rec["opened_by"] == "critic"
    assert ob_rec["evidence"] == ["evidence/strings/hits.txt#L1"]
    # it really landed in the ledger, not just in the return value.
    open_obs = ob.list_obligations(ws, status="open")
    assert len(open_obs) == 1 and open_obs[0]["id"] == ob_rec["id"]


@pytest.mark.parametrize("verdict,rationale,match", [
    ("maybe", "r", "critic_verdict must be one of"),
    ("supported", "", "needs a rationale"),
    ("supported", "   ", "needs a rationale"),
])
def test_bad_critic_input_refused(ws, verdict, rationale, match):
    with pytest.raises(critic.CriticError, match=match):
        critic.review(ws, SUPPORTED_FINDING, critic_verdict=verdict, critic_rationale=rationale)


def test_finding_cannot_close_an_open_obligation_as_a_side_effect(ws):
    opened = ob.open_obligation(ws, "Is partition 2 signed?", opened_by="strings")
    finding = {**SUPPORTED_FINDING, "closes": [opened["id"]]}
    result = critic.review(ws, finding, critic_verdict="supported", critic_rationale="closes it")
    assert result["verdict"] == critic.VERDICT_REJECTED
    assert any("claims to close open obligation" in r and opened["id"] in r for r in result["reasons"])
    # the obligation this finding tried to fold in is still open -- only the
    # explicit obligations.resolve()/waive() path may close it.
    assert ob.list_obligations(ws, status="open")[0]["id"] == opened["id"]


def test_closes_a_non_open_obligation_is_fine(ws):
    opened = ob.open_obligation(ws, "Is partition 2 signed?", opened_by="strings")
    ob.waive(ws, opened["id"], rationale="out of scope for this engagement")
    finding = {**SUPPORTED_FINDING, "closes": [opened["id"]]}
    result = critic.review(ws, finding, critic_verdict="supported", critic_rationale="unrelated to the waived item")
    assert result["verdict"] == critic.VERDICT_ACCEPTED


def test_evidence_entry_shapes(ws):
    assert critic.deterministic_check(ws, {
        "id": "F-003", "evidence": ["evidence/strings/hits.txt#L1"],
    }) == {"ok": True, "reasons": [], "evidence_refs": ["evidence/strings/hits.txt#L1"]}
    bad = critic.deterministic_check(ws, {"id": "F-004", "evidence": [{"locator": "no path"}]})
    assert not bad["ok"] and "unusable evidence entry" in bad["reasons"][0]
    assert critic.deterministic_check(ws, {"id": "F-005", "evidence": []})["ok"] is False


def test_non_dict_finding_refused(ws):
    with pytest.raises(critic.CriticError, match="must be an object"):
        critic.deterministic_check(ws, ["not", "a", "finding"])


def test_cli_critic_review_exit_codes(ws, tmp_path, capsys):
    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(json.dumps(UNSUPPORTED_FINDING))
    assert wc.main(["critic", "review", ws, str(unsupported),
                    "--verdict", "supported", "--rationale", "x"]) == 2
    assert "cites no evidence" in capsys.readouterr().err

    supported = tmp_path / "supported.json"
    supported.write_text(json.dumps(SUPPORTED_FINDING))
    assert wc.main(["--json", "critic", "review", ws, str(supported),
                    "--verdict", "supported", "--rationale", "checks out"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verdict"] == "accepted"

    assert wc.main(["critic", "review", ws, str(supported),
                    "--verdict", "falsified", "--rationale", "wrong partition"]) == 3
    assert ob.list_obligations(ws, status="open")

    assert wc.main(["critic", "review", ws, str(tmp_path / "missing.json"),
                    "--verdict", "supported", "--rationale", "x"]) == 1
