"""wq#3760: proposal→wq bridge, rejection ledger, weekly cap (design/wq3755 §4-§6).

Every test drives the real /home/lumen/bin/wq against a throwaway queue.db (WQ_DB), so a
filed ticket, its status and its created_at are wq's own, not a stub's. Evidence files are
written schema-valid by hand here: the gate that produces them is test_sandbox_gate.py's
subject, except test_gated_proposal_end_to_end which runs it for real.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

RESEARCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RESEARCH)
import bridge  # noqa: E402
import ledger  # noqa: E402
import sandbox_gate  # noqa: E402

PASS = os.path.join(RESEARCH, "fixtures", "sandbox", "pass_smoke.json")
WQ = "/home/lumen/bin/wq"
UTC = datetime.timezone.utc


@pytest.fixture
def q(tmp_path, monkeypatch):
    """A fresh queue.db initialised by wq itself, plus evidence/overflow dirs."""
    db = str(tmp_path / "queue.db")
    monkeypatch.setenv("WQ_DB", db)
    monkeypatch.setenv("FOREMAN_JUDGE_SHADOW", "0")
    monkeypatch.delenv("WQ_AGENT", raising=False)
    monkeypatch.setattr(bridge, "WQ", WQ)
    subprocess.run([WQ, "list"], capture_output=True, check=True)
    (tmp_path / "ev").mkdir()
    return {"db": db, "ev": str(tmp_path / "ev"), "of": str(tmp_path / "overflow"),
            "dir": tmp_path}


def proposal(q, n, title=None, ts=None):
    """Proposal n with passing container evidence stamped `ts` (default: now)."""
    prop = {"id": f"prop-{n}", "title": title or f"Install tool number {n}", "arch": "amd64",
            "install": "true", "smoke": "true", "brief_id": f"brief-{n}", "trigger": "manual"}
    path = q["dir"] / f"prop-{n}.json"
    path.write_text(json.dumps(prop))
    ts = ts or datetime.datetime.now(UTC)
    ev = {"proposal_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
          "image": "debian:bookworm-slim", "arch": "amd64", "install_exit": 0,
          "smoke_cmd": "true", "smoke_exit": 0, "duration_s": 1.0, "stdout_tail": "",
          "backend": "container", "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(os.path.join(q["ev"], f"prop-{n}.json"), "w") as f:
        json.dump(ev, f)
    return str(path)


def submit(q, path, **kw):
    return bridge.submit(path, q["db"], q["ev"], overflow_dir=q["of"], **kw)


def rows(q, sql, *args):
    c = sqlite3.connect(q["db"])
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def test_submit_files_frank_gated_ticket_in_pending_review(q):
    code, out = submit(q, proposal(q, 1))
    assert (code, out["verdict"]) == (0, "submitted"), out
    assert "warning" not in out, out
    (status, gate_type, title, detail, prompt), = rows(
        q, "SELECT status, gate_type, title, detail, decision_prompt FROM tasks WHERE id=?",
        out["wq_id"])
    assert (status, gate_type) == ("pending_review", "frank_decision")
    assert title == "research: Install tool number 1"
    assert f"Evidence: {os.path.join(q['ev'], 'prop-1.json')}" in detail
    assert "brief_id=brief-1" in detail and "Trigger: manual" in detail
    assert prompt.startswith("Approve research proposal")
    first = out["wq_id"]
    code, out = submit(q, proposal(q, 1))
    assert (code, out["reasons"]) == (1, [f"already submitted as wq#{first}"])


def test_seeded_week_parks_the_sixth(q):
    for n in range(1, 6):
        assert submit(q, proposal(q, n))[0] == 0
    sixth = proposal(q, 6)
    assert submit(q, sixth, dry_run=True)[1]["verdict"] == "would_park"
    assert not os.path.exists(q["of"])
    code, out = submit(q, sixth)
    assert (code, out["verdict"], out["week_count"]) == (3, "parked", 5)
    assert open(out["parked_at"], "rb").read() == open(sixth, "rb").read()
    assert rows(q, "SELECT COUNT(*) FROM tasks")[0][0] == 5


def test_cap_is_per_iso_week(q):
    """Same five tickets, moved back a week: the 6th is now under cap and files."""
    for n in range(1, 6):
        assert submit(q, proposal(q, n))[0] == 0
    c = sqlite3.connect(q["db"])
    c.execute("UPDATE tasks SET created_at=datetime(created_at, '-7 days')")
    c.commit()
    c.close()
    code, out = submit(q, proposal(q, 6))
    assert (code, out["verdict"], out["week_count"]) == (0, "submitted", 1), out


def test_iso_week_bounds():
    sun = datetime.datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
    mon = datetime.datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    assert bridge.iso_week_bounds(sun) == ("2026-10-12 00:00:00", "2026-10-19 00:00:00")
    assert bridge.iso_week_bounds(mon)[0] == "2026-10-19 00:00:00"


def test_reject_writes_ledger_row_the_next_brief_contains(q):
    path = proposal(q, 1, title="Adopt  FooTool for log shipping")
    tid = submit(q, path)[1]["wq_id"]
    r = subprocess.run([WQ, "answer", str(tid), "No: journald forwarding already covers it"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    code, out = bridge.reject(tid, "already-solved", db=q["db"])
    assert (code, out["verdict"]) == (0, "rejected"), out
    (ts, sha, title, klass, reason, brief), = rows(
        q, "SELECT ts, proposal_sha256, title, reject_class, frank_reason, brief_id "
           "FROM rejections WHERE id=?", out["ledger_row"])
    assert sha == hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert (title, klass, brief) == ("Adopt FooTool for log shipping", "already-solved",
                                     "brief-1")
    assert reason == "No: journald forwarding already covers it"
    assert rows(q, "SELECT status FROM tasks WHERE id=?", tid)[0][0] == "done"
    c = ledger.connect(q["db"])
    block = ledger.constraint_block(c)
    c.close()
    assert block == ("DO NOT re-propose: Adopt FooTool for log shipping (already-solved): "
                     "No: journald forwarding already covers it")


def test_constraint_block_newest_first_capped_at_50(q):
    c = ledger.connect(q["db"])
    for n in range(55):
        ledger.record(c, f"{n:064x}", f"title {n}", "noise", f"reason {n}")
    lines = ledger.constraint_block(c).splitlines()
    c.close()
    assert len(lines) == 50
    assert lines[0] == "DO NOT re-propose: title 54 (noise): reason 54"
    assert lines[-1].startswith("DO NOT re-propose: title 5 ")


def test_ledger_is_a_hard_filter_at_the_bridge(q):
    c = ledger.connect(q["db"])
    ledger.record(c, "0" * 64, "install tool NUMBER 1", "duplicate", "we have one")
    c.close()
    code, out = submit(q, proposal(q, 1))
    assert code == 1 and out["reasons"] == ["rejection ledger row 1 matches this proposal"]
    assert submit(q, proposal(q, 2))[0] == 0


def test_ledger_filters_before_sandbox_spend(q, monkeypatch):
    seen = []
    monkeypatch.setitem(sandbox_gate.BACKENDS, "container",
                        lambda prop, deadline: seen.append(prop["id"]) or ("f", 0, "", 0, ""))
    inbox, gated = q["dir"] / "in", q["dir"] / "gated"
    inbox.mkdir()
    for n in (1, 2):
        os.rename(proposal(q, n), inbox / f"p{n}.json")
    sha2 = hashlib.sha256((inbox / "p2.json").read_bytes()).hexdigest()
    c = ledger.connect(q["db"])
    ledger.record(c, sha2, "something else entirely", "noise", "no")
    c.close()
    assert sandbox_gate.drain(str(inbox), str(gated), q["ev"], q["db"]) == 0
    assert seen == ["prop-1"]
    assert sorted(os.listdir(gated)) == ["p1.json", "p2.json"]


def test_reject_refusals(q):
    tid = submit(q, proposal(q, 1))[1]["wq_id"]
    assert bridge.reject(tid, "Bad Class!", reason="x", db=q["db"])[0] == 1
    assert bridge.reject(tid, "noise", db=q["db"])[1]["reasons"] == [
        "no frank_response on the ticket and no --reason"]
    r = subprocess.run([WQ, "add", "--title", "plain ticket"], capture_output=True, text=True)
    plain = int(r.stdout.split("added task ")[1].split()[0])
    assert "not a research-bridge ticket" in bridge.reject(plain, "noise", "x", q["db"])[1][
        "reasons"][0]
    assert rows(q, "SELECT COUNT(*) FROM rejections")[0][0] == 0


def test_unverified_evidence_never_reaches_wq(q):
    path = proposal(q, 1, ts=datetime.datetime.now(UTC) - datetime.timedelta(days=8))
    code, out = submit(q, path)
    assert code == 1 and out["reasons"][0].startswith("evidence stale")
    assert rows(q, "SELECT COUNT(*) FROM tasks")[0][0] == 0


@pytest.mark.skipif(not os.path.exists("/usr/bin/bwrap") or sandbox_gate.host_arch() != "amd64",
                    reason="local-bwrap needs bubblewrap on an amd64 host")
def test_gated_proposal_end_to_end(q):
    sandbox_gate.gate(PASS, "local-bwrap", q["ev"])
    assert submit(q, PASS)[0] == 1  # local-bwrap evidence is not the real gate
    code, out = submit(q, PASS, accepted=("local-bwrap",))
    assert (code, out["verdict"]) == (0, "submitted"), out


def test_cli_constraints(q, capsys):
    c = ledger.connect(q["db"])
    ledger.record(c, "1" * 64, "T", "noise", "R")
    c.close()
    assert bridge.main(["constraints", "--db", q["db"]]) == 0
    assert capsys.readouterr().out == "DO NOT re-propose: T (noise): R\n"
