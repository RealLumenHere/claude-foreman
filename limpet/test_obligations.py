"""wq#3965 -- tests for the per-workspace reconstruction obligation ledger.

Covers the ticket's acceptance set: open -> resolve, resolve-without-evidence refused,
done refused while an item is open, and the report rendering the ledger. The evidence
boundary is probed with refs from OUTSIDE the workspace (an existing sibling file via
``..``, an absolute path), so a containment bug can actually fail a test (wq#2630).
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obligations as ob  # noqa: E402
import workspace_cli as wc  # noqa: E402
import workspace_report as wr  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
EV_ID = "ev_" + "a" * 64


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "evidence" / "ghidra").mkdir(parents=True)
    (w / "evidence" / "ghidra" / "fn_3c.c").write_text("int fn_3c(int len) { return len & 0x7f; }\n")
    (w / "manifest.json").write_text(json.dumps({"target_id": "acme-router-v2", "class": "firmware"}))
    _git("-C", str(w), "init", "-q")
    _git("-C", str(w), "add", "-A")
    _git("-C", str(w), "commit", "-q", "-m", "init")
    (tmp_path / "outside.txt").write_text("not workspace evidence\n")
    return str(w)


def _subjects(w):
    return _git("-C", w, "log", "--pretty=%s").splitlines()


def test_open_then_resolve_commits_ledger(ws):
    o = ob.open_obligation(ws, "What does fn_3c do with the length byte?", opened_by="static/ghidra",
                           severity="high", now="2026-01-01T00:00:00Z")
    assert o["id"] == "OB-001" and o["status"] == "open" and o["revision"] == 1
    assert ob.list_obligations(ws, status="open")[0]["id"] == "OB-001"

    r = ob.resolve(ws, "OB-001", evidence=["evidence/ghidra/fn_3c.c#L1", EV_ID],
                   rationale="masks to 7 bits", expected_revision=1, now="2026-01-01T01:00:00Z")
    assert r["status"] == "resolved" and r["revision"] == 2
    assert r["resolution"] == {"disposition": "verified", "rationale": "masks to 7 bits",
                               "evidence": ["evidence/ghidra/fn_3c.c#L1", EV_ID],
                               "ts": "2026-01-01T01:00:00Z", "by": "agent"}
    assert [h["action"] for h in r["history"]] == ["open", "resolved"]
    # The ledger is git-committed with the workspace and the tree is clean afterwards.
    assert _subjects(ws)[:2] == ["obligations: resolved OB-001",
                                 "obligations: open OB-001: What does fn_3c do with the length byte?"]
    assert _git("-C", ws, "status", "--porcelain") == ""
    on_disk = json.loads(_git("-C", ws, "show", "HEAD:obligations.json"))
    assert on_disk["obligations"][0]["status"] == "resolved"


@pytest.mark.parametrize("evidence,match", [
    ([], "without evidence"),
    (["evidence/ghidra/missing.c"], "not found"),
    (["../outside.txt"], "escapes the workspace"),
    (["evidence/../../outside.txt"], "escapes the workspace"),
    ([os.path.abspath(__file__)], "absolute"),
    (["ev_nothex"], "not found"),
    (["  "], "empty"),
])
def test_resolve_without_real_evidence_refused(ws, evidence, match):
    ob.open_obligation(ws, "Is partition 2 signed?", opened_by="extract/unblob")
    before = open(ob.ledger_path(ws)).read()
    with pytest.raises(ob.ObligationError, match=match):
        ob.resolve(ws, "OB-001", evidence=evidence, rationale="trust me")
    assert open(ob.ledger_path(ws)).read() == before
    assert ob.list_obligations(ws)[0]["status"] == "open"


def test_outside_ref_exists_so_refusal_is_containment(ws):
    # The ".." probe target really exists; refusing it proves the containment check, not a miss.
    assert os.path.exists(os.path.join(ws, "..", "outside.txt"))


def test_done_refused_while_open_then_allowed(ws):
    ob.open_obligation(ws, "Q1", opened_by="static")
    ob.open_obligation(ws, "Q2", opened_by="emulate")
    with pytest.raises(ob.ObligationsOpen) as exc:
        ob.require_done(ws)
    assert [o["id"] for o in exc.value.open_items] == ["OB-001", "OB-002"]

    ob.resolve(ws, "OB-001", evidence=["evidence/ghidra/fn_3c.c"], rationale="answered")
    with pytest.raises(ob.ObligationsOpen):
        ob.require_done(ws)  # one still open, no waiver
    with pytest.raises(ob.ObligationError, match="rationale"):
        ob.waive(ws, "OB-002", rationale="")
    ob.waive(ws, "OB-002", rationale="emulation out of scope for this engagement")
    res = ob.require_done(ws)
    assert res["ok"] and res["summary"] == {"total": 2, "open": 0, "resolved": 1, "waived": 1,
                                            "done_ready": True}


def test_empty_ledger_is_done(ws):
    assert ob.check_done(ws)["ok"] and not os.path.exists(ob.ledger_path(ws))


def test_stale_revision_and_double_close_refused(ws):
    ob.open_obligation(ws, "Q", opened_by="static")
    with pytest.raises(ob.ObligationError, match="revision 1, not 5"):
        ob.waive(ws, "OB-001", rationale="r", expected_revision=5)
    ob.waive(ws, "OB-001", rationale="r", expected_revision=1)
    with pytest.raises(ob.ObligationError, match="already waived"):
        ob.resolve(ws, "OB-001", evidence=[EV_ID], rationale="r")
    with pytest.raises(ob.ObligationError, match="no such obligation"):
        ob.waive(ws, "OB-404", rationale="r")


def test_corrupt_ledger_fails_closed(ws):
    with open(ob.ledger_path(ws), "w") as fh:
        fh.write("[]")
    with pytest.raises(ob.ObligationError):
        ob.check_done(ws)
    with pytest.raises(wr.ReportError):
        wr.collect(ws, source_date=0.0)


def test_workspace_git_dir_layout_commits(tmp_path):
    # The design layout keeps history in <ws>/workspace.git, not .git.
    w = tmp_path / "ws2"
    (w / "evidence").mkdir(parents=True)
    (w / "evidence" / "log.txt").write_text("x\n")
    gd = str(w / "workspace.git")
    _git("init", "-q", "--bare", gd)
    _git("--git-dir", gd, "--work-tree", str(w), "add", "evidence")
    _git("--git-dir", gd, "--work-tree", str(w), "commit", "-q", "-m", "init")
    ob.open_obligation(str(w), "Q", opened_by="static", evidence=["evidence/log.txt"])
    assert _git("--git-dir", gd, "log", "-1", "--pretty=%s").strip() == "obligations: open OB-001: Q"
    assert "obligations.json" in _git("--git-dir", gd, "ls-tree", "--name-only", "HEAD")


def test_no_git_workspace_writes_without_commit(tmp_path):
    w = tmp_path / "bare"
    w.mkdir()
    ob.open_obligation(str(w), "Q", opened_by="static")
    assert ob.list_obligations(str(w))[0]["id"] == "OB-001"


def test_cli_verbs_and_verify_gate(ws, capsys):
    assert wc.main(["obligations", "open", ws, "Is the bootloader locked?", "--opened-by", "probe/jtag"]) == 0
    assert wc.main(["obligations", "verify", ws]) == 3
    assert "REFUSED" in capsys.readouterr().err
    assert wc.main(["obligations", "resolve", ws, "OB-001", "--rationale", "r"]) == 2
    assert "without evidence" in capsys.readouterr().err
    assert wc.main(["--json", "obligations", "resolve", ws, "OB-001", "--rationale", "OTP fuse read",
                    "--evidence", "evidence/ghidra/fn_3c.c"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["obligation"]["status"] == "resolved"
    assert wc.main(["--json", "obligations", "list", ws, "--status", "open"]) == 0
    assert json.loads(capsys.readouterr().out)["obligations"] == []
    assert wc.main(["obligations", "verify", ws]) == 0
    assert "OK" in capsys.readouterr().out


def test_report_renders_ledger(ws):
    secret = "gh" "p_" + "B" * 36
    ob.open_obligation(ws, f"Is <token> {secret} live?", opened_by="strings", now="2026-01-01T00:00:00Z")
    ob.open_obligation(ws, "What is the 0x3c handler?", opened_by="static/ghidra", now="2026-01-01T00:00:01Z")
    ob.resolve(ws, "OB-002", evidence=["evidence/ghidra/fn_3c.c#L1"], rationale="length mask",
               now="2026-01-01T00:00:02Z")
    out = str(os.path.join(ws, "..", "out"))
    st = wr.export(ws, formats=("md", "html", "json"), out_dir=out, source_date=0.0)
    md = open(st["written"]["md"]).read()
    html = open(st["written"]["html"]).read()
    ir = json.load(open(st["written"]["json"]))

    assert "## Reconstruction obligations" in md
    assert "**1 open** · 1 resolved · 0 waived" in md and "not done" in md
    assert "`OB-002` | resolved" in md and "length mask" in md and "`evidence/ghidra/fn_3c.c#L1`" in md
    assert "Reconstruction obligations</h2>" in html and "&lt;token&gt;" in html and "<token>" not in html
    assert ir["obligations_summary"]["open"] == 1 and len(ir["obligations"]) == 2
    assert {e["ref"] for e in ir["timeline"] if e["kind"] == "obligation"} == {"OB-001", "OB-002"}
    for text in (md, html, json.dumps(ir)):
        assert secret not in text

    summary = wr.export(ws, formats=("md",), template="exec-summary", out_dir=out + "-x", source_date=0.0)
    exec_md = open(summary["written"]["md"]).read()
    assert "**Obligations:** 1 open" in exec_md and "OB-001" in exec_md and "OB-002" not in exec_md


def test_report_without_ledger_says_none(ws):
    st = wr.export(ws, formats=("md",), out_dir=os.path.join(ws, "..", "o"), source_date=0.0)
    assert "_No obligations recorded._" in open(st["written"]["md"]).read()
