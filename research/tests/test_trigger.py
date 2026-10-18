"""wq#3761: research trigger service — needs-tag/alert/manual inputs, brief
formulation, bounce-or-drop dispatch (design/wq3755 §2+§5).

Every wq-input test drives the real /home/lumen/bin/wq against a throwaway queue.db
(WQ_DB), so a ticket's status/detail/needs are wq's own, not a stub's — same
discipline as test_bridge.py.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

RESEARCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RESEARCH)
import ledger  # noqa: E402
import trigger  # noqa: E402

WQ = "/home/lumen/bin/wq"
NEEDS_MIGRATION = "/home/lumen/bin/migrations/wq1331_needs.py"
ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_ts TEXT NOT NULL, last_ts TEXT NOT NULL,
  source TEXT NOT NULL, key TEXT NOT NULL,
  title TEXT, message TEXT, priority TEXT DEFAULT 'default',
  count INTEGER DEFAULT 1, status TEXT DEFAULT 'open',
  owner TEXT DEFAULT 'unassigned', note TEXT, resolved_ts TEXT);
"""


@pytest.fixture
def q(tmp_path, monkeypatch):
    db = str(tmp_path / "queue.db")
    monkeypatch.setenv("WQ_DB", db)
    monkeypatch.delenv("WQ_AGENT", raising=False)
    subprocess.run([WQ, "list"], capture_output=True, check=True, env={**os.environ, "WQ_DB": db})
    briefs = tmp_path / "briefs"
    pending = tmp_path / "pending"
    return {"db": db, "dir": tmp_path, "briefs": str(briefs), "pending": str(pending)}


def wq_add(q, *, title="fixture ticket", needs=None, detail=None):
    args = [WQ, "add", "--title", title]
    if detail:
        args += ["--detail", detail]
    r = subprocess.run(args, capture_output=True, text=True, env={**os.environ, "WQ_DB": q["db"]})
    assert r.returncode == 0, r.stdout + r.stderr
    tid = int(r.stdout.split("added task ")[1].split()[0])
    if needs is not None:
        r = subprocess.run([WQ, "edit", str(tid), "--needs", needs], capture_output=True,
                           text=True, env={**os.environ, "WQ_DB": q["db"]})
        assert r.returncode == 0, r.stdout + r.stderr
    return tid


def seed_ledger(q, *rows):
    c = ledger.connect(q["db"])
    for sha, title, klass, reason in rows:
        ledger.record(c, sha, title, klass, reason)
    c.close()


def test_research_tag_via_needs_line_yields_unserved_question(q):
    tid = wq_add(q, detail="Needs: research(does qemu-user support gfx1013 passthrough?)")
    out = trigger.unserved_wq_questions(q["db"], q["briefs"])
    assert out == [(f"wq{tid}", "does qemu-user support gfx1013 passthrough?", f"wq#{tid}")]


def test_research_tag_with_parenthetical_aside_is_not_truncated(q):
    # wq#4204: the shared tag parser used to stop at the first ')', so a question
    # with a parenthetical aside yielded a mangled "primary search query".
    tid = wq_add(q, detail="Needs: research(does gfx1013 (RDNA3) need a passthrough quirk?)")
    out = trigger.unserved_wq_questions(q["db"], q["briefs"])
    assert out == [(f"wq{tid}",
                    "does gfx1013 (RDNA3) need a passthrough quirk?", f"wq#{tid}")]


def test_research_tag_via_needs_column_is_also_read(q):
    """harness/needs.py's vocabulary (ssh/root/.../peer) doesn't list 'research', but
    the parser 'accepts anything tag-shaped' (needs.py:18) — this is the designed
    escape hatch so a future wq#1331 vocabulary bump isn't a prerequisite here."""
    r = subprocess.run([sys.executable, NEEDS_MIGRATION, "--db", q["db"], "--yes"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([WQ, "add", "--title", "t"], capture_output=True, text=True,
                       env={**os.environ, "WQ_DB": q["db"]})
    tid = int(r.stdout.split("added task ")[1].split()[0])
    c = sqlite3.connect(q["db"])
    c.execute("UPDATE tasks SET needs=? WHERE id=?",
              ("research(why is nephthys vulkan pp512 only 321 t/s?)", tid))
    c.commit()
    c.close()
    out = trigger.unserved_wq_questions(q["db"], q["briefs"])
    assert out == [(f"wq{tid}", "why is nephthys vulkan pp512 only 321 t/s?", f"wq#{tid}")]


def test_closed_ticket_is_not_a_trigger(q):
    tid = wq_add(q, detail="Needs: research(irrelevant once done)")
    subprocess.run([WQ, "done", str(tid), "--no-artifact", "closed"],
                  capture_output=True, env={**os.environ, "WQ_DB": q["db"]})
    assert trigger.unserved_wq_questions(q["db"], q["briefs"]) == []


def test_already_briefed_ticket_is_served(q):
    tid = wq_add(q, detail="Needs: research(served already?)")
    os.makedirs(q["briefs"])
    open(os.path.join(q["briefs"], f"wq{tid}.md"), "w").close()
    assert trigger.unserved_wq_questions(q["db"], q["briefs"]) == []


def test_aq_alert_owner_research_yields_unserved_question(tmp_path):
    aq_db = str(tmp_path / "alerts.db")
    c = sqlite3.connect(aq_db)
    c.executescript(ALERTS_SCHEMA)
    c.execute("INSERT INTO alerts(first_ts, last_ts, source, key, title, message, owner) "
              "VALUES('t','t','probe','k','disk pressure on sobek','87% and rising','research')")
    c.commit()
    c.close()
    briefs = str(tmp_path / "briefs")
    out = trigger.unserved_aq_alerts(aq_db, briefs)
    assert out == [("aq1", "disk pressure on sobek 87% and rising", "aq#1")]


def test_aq_alert_not_owned_by_research_is_ignored(tmp_path):
    aq_db = str(tmp_path / "alerts.db")
    c = sqlite3.connect(aq_db)
    c.executescript(ALERTS_SCHEMA)
    c.execute("INSERT INTO alerts(first_ts, last_ts, source, key, title, owner) "
              "VALUES('t','t','probe','k','unrelated','ops')")
    c.commit()
    c.close()
    assert trigger.unserved_aq_alerts(aq_db, str(tmp_path / "briefs")) == []


def test_brief_contains_seeded_ledger_constraint_lines(q):
    """The probe (ticket wq#3761 / design §7 item 3): a research(...) tag on a
    fixture ticket yields a brief containing the seeded rejection ledger's
    constraint lines, prepended verbatim ahead of the question."""
    seed_ledger(q, ("a" * 64, "Adopt FooTool for log shipping", "already-solved",
                    "journald forwarding already covers it"),
               ("b" * 64, "Install BarAgent everywhere", "duplicate", "we have one"))
    tid = wq_add(q, detail="Needs: research(what ships gfx1013 vulkan support?)")
    out = trigger.unserved_wq_questions(q["db"], q["briefs"])
    bid, question, ctx = out[0]
    report = trigger.trigger_one(bid, question, ctx, q["db"], q["briefs"], q["pending"],
                                 dry_run=True)
    assert report["verdict"] == "would_dispatch"
    text = open(report["brief_path"]).read()
    assert text.index("DO NOT re-propose: Install BarAgent everywhere") < text.index("Question:")
    assert "DO NOT re-propose: Adopt FooTool for log shipping (already-solved): " \
           "journald forwarding already covers it" in text
    assert f"Question: {question}" in text
    assert f"Requesting context: {ctx}" in text


def test_dry_run_writes_brief_but_does_not_dispatch(q):
    report = trigger.ask("why is X slow", "manual", q["db"], q["briefs"], q["pending"],
                         dry_run=True)
    assert report["verdict"] == "would_dispatch"
    assert os.path.exists(report["brief_path"])
    assert not os.path.exists(q["pending"])


def test_draft_with_no_smoke_test_bounces_once_then_drops(q, monkeypatch):
    calls = []

    def fake_chat(model, system, user, effort):
        calls.append(user)
        return json.dumps({"id": "no-smoke", "title": "t", "arch": "amd64", "install": "true"})

    report = trigger.ask("needs a tool", "manual", q["db"], q["briefs"], q["pending"],
                         dry_run=False, chat=fake_chat)
    assert report["verdict"] == "dropped"
    assert len(calls) == 2
    assert trigger.BOUNCE_NOTE.strip() not in calls[0]
    assert calls[1] == calls[0] + trigger.BOUNCE_NOTE  # bounce appends the correction
    assert not os.path.exists(q["pending"])


def test_runnable_draft_is_submitted_to_sandbox_inbox(q, monkeypatch):
    def fake_chat(model, system, user, effort):
        return json.dumps({"id": "widget-tool", "title": "Install widget", "arch": "amd64",
                           "install": "true", "smoke": "true"})

    report = trigger.ask("needs a tool", "manual", q["db"], q["briefs"], q["pending"],
                         dry_run=False, chat=fake_chat)
    assert report["verdict"] == "dispatched"
    prop = json.load(open(report["proposal_path"]))
    assert prop["id"] == "widget-tool"
    assert prop["brief_id"] == report["brief_id"]
    assert os.path.dirname(report["proposal_path"]) == q["pending"]


def test_proposal_with_unsafe_id_falls_back_to_brief_id(q):
    def fake_chat(model, system, user, effort):
        return json.dumps({"id": "Not An ID!", "title": "t", "arch": "amd64",
                           "install": "true", "smoke": "true"})

    report = trigger.ask("needs a tool", "manual", q["db"], q["briefs"], q["pending"],
                         dry_run=False, chat=fake_chat)
    assert report["verdict"] == "dispatched"
    assert os.path.basename(report["proposal_path"]) == f"{report['brief_id'].lower()}.json"


def test_dead_gateway_refuses_cleanly(q):
    def dead_chat(model, system, user, effort):
        return None

    report = trigger.ask("needs a tool", "manual", q["db"], q["briefs"], q["pending"],
                         dry_run=False, chat=dead_chat)
    assert report["verdict"] == "dropped"
