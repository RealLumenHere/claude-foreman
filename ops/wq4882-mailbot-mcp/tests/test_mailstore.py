"""wq#4882: mailbot MCP adapter — read-only mirror access, draft gate, no send surface, no tokens."""
import asyncio
import builtins
import os
import re
import select
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import mailstore  # noqa: E402

# Pinned copy of the mirror schema (mailbot mail_recall.py SCHEMA + mail_tasks.py SCHEMA, 2026-11-18).
SCHEMA = """
CREATE TABLE threads (thread_id TEXT PRIMARY KEY, contact_email TEXT, contact_name TEXT,
  subject TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE messages (id TEXT PRIMARY KEY, thread_id TEXT, direction TEXT, ts TEXT,
  from_addr TEXT, to_addr TEXT, body TEXT, probe INTEGER, leak_flags TEXT, regenerated INTEGER, needs_frank INTEGER);
CREATE TABLE contact_notes (contact_email TEXT PRIMARY KEY, contact_name TEXT,
  summary TEXT, updated_at TEXT, boundary_reminders TEXT, honesty_reminders TEXT);
CREATE TABLE tasks (id TEXT PRIMARY KEY, thread_id TEXT, inbound_id TEXT, ack_id TEXT,
  contact_email TEXT, contact_name TEXT, subject TEXT, category TEXT, body TEXT, status TEXT,
  answer_body TEXT, created_at TEXT, answered_at TEXT, sent_at TEXT, send_attempts INTEGER);
"""
_now = datetime.now(timezone.utc)
NOW = (_now - timedelta(days=1)).isoformat()
LATER = (_now - timedelta(hours=23)).isoformat()
OLD = (_now - timedelta(days=200)).isoformat()


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "mirror" / "mail.db"
    db.parent.mkdir()
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO threads VALUES ('t1','ann@example.org','Ann','Quote for roof',?,?)", (OLD, NOW))
    c.execute("INSERT INTO threads VALUES ('t2','bob@example.org','Bob','Hello',?,?)", (OLD, OLD))
    rows = [
        ("m1", "t1", "in", NOW, "ann@example.org", "bot", "Need a roof quote\n- - - - -\nquoted SECRETHISTORY", 0, 1),
        ("m2", "t1", "out", LATER, "bot", "ann@example.org", "Got it, back to you soon", 0, 0),
        ("m3", "t2", "in", OLD, "bob@example.org", "bot", "roof talk from january", 1, 0),
    ]
    c.executemany("INSERT INTO messages (id,thread_id,direction,ts,from_addr,to_addr,body,probe,needs_frank) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.execute("INSERT INTO contact_notes VALUES ('ann@example.org','Ann','Wants a roof quote',?,'[]','[]')", (NOW,))
    c.execute("INSERT INTO tasks (id,thread_id,inbound_id,contact_email,contact_name,subject,category,body,status,"
              "created_at) VALUES ('k1','t1','m1','ann@example.org','Ann','Quote for roof','quote','roof?','pending',?)",
              (NOW,))
    c.execute("INSERT INTO tasks (id,thread_id,inbound_id,contact_email,subject,category,body,status,created_at) "
              "VALUES ('k2','t2','m3','bob@example.org','Hello','callback','call me','sent',?)", (OLD,))
    c.commit()
    c.close()
    monkeypatch.setattr(mailstore, "MAIL_DB", str(db))
    monkeypatch.setattr(mailstore, "DRAFTS_DB", str(tmp_path / "drafts.db"))
    monkeypatch.setattr(mailstore, "VERIFIER", str(tmp_path / "approve" / "verifier.json"))
    monkeypatch.setattr(mailstore, "FAIL_DELAY", 0)
    monkeypatch.setattr(mailstore, "SCRYPT_N", 2 ** 10)  # fast in tests; verifier records its own n
    return tmp_path


def test_search_filters_by_query_contact_and_window(store):
    hits = mailstore.search("roof", since_days=30)
    # m2 matches through its thread subject; m3 matches 'roof' but is outside the window
    assert [h["id"] for h in hits] == ["m2", "m1"]
    assert "SECRETHISTORY" not in hits[1]["body"] and hits[1]["needs_frank"] is True
    assert mailstore.search("SECRETHISTORY") != []  # quoted tail is searchable, just not returned
    assert [h["id"] for h in mailstore.search("january", since_days=400)] == ["m3"]
    assert {h["id"] for h in mailstore.search(contact="ANN")} == {"m1", "m2"}
    assert len(mailstore.search(since_days=400, limit=10_000)) == 3  # clamp, not error


def test_thread_contact_needs_frank_status(store):
    t = mailstore.thread("t1")
    assert t["found"] and [m["id"] for m in t["messages"]] == ["m1", "m2"]
    assert mailstore.thread("nope") == {"found": False, "thread_id": "nope"}
    assert mailstore.contact("ann")["notes"]["summary"] == "Wants a roof quote"
    assert mailstore.contact("zed")["found"] is False
    assert [m["id"] for m in mailstore.needs_frank()] == ["m1"]
    assert mailstore.mirror_status()["pending_tasks"] == 1


def test_mirror_is_opened_read_only(store):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        mailstore._mail().execute("DELETE FROM messages")
    mailstore.create_draft("k1", "Roof quote is $X")
    assert sqlite3.connect(mailstore.MAIL_DB).execute("SELECT count(*) FROM messages").fetchone()[0] == 3


def test_draft_lifecycle_and_refusals(store):
    d = mailstore.create_draft("k1", "  Roof quote: 4k  ")
    assert d["status"] == "draft" and d["to"] == "ann@example.org"
    assert os.stat(mailstore.DRAFTS_DB).st_mode & 0o777 == 0o600
    assert [x["body"] for x in mailstore.list_drafts()] == ["Roof quote: 4k"]
    for task, body, msg in [("nope", "x", "no mailbot task"), ("k2", "x", "already answered"),
                            ("k1", "   ", "empty"), ("k1", "x" * 9000, "exceeds")]:
        with pytest.raises(ValueError, match=msg):
            mailstore.create_draft(task, body)


def test_approved_task_leaves_pending_and_blocks_new_drafts(store):
    d = mailstore.create_draft("k1", "answer")
    mailstore._decide(d["draft_id"], "approved")
    assert mailstore.pending_tasks() == []
    with pytest.raises(ValueError, match="already answered"):
        mailstore.create_draft("k1", "second answer")


PASS = "correct horse battery staple"


@pytest.fixture
def fake_mailbot(tmp_path, monkeypatch):
    """A mail_tasks stand-in that records answer() calls to a file instead of writing D1."""
    d = tmp_path / "fake_mailbot"
    d.mkdir()
    sent = tmp_path / "sent.log"
    (d / "mail_tasks.py").write_text(
        "def answer(task_id, text):\n"
        f"    open({str(sent)!r}, 'a').write(task_id + '|' + text + '\\n')\n")
    monkeypatch.setattr(mailstore, "MAILBOT", str(d))
    monkeypatch.delitem(sys.modules, "mail_tasks", raising=False)
    return sent


def _draft_status(did):
    return sqlite3.connect(mailstore.DRAFTS_DB).execute("SELECT status FROM drafts WHERE id=?", (did,)).fetchone()[0]


def test_approve_refuses_without_tty_and_never_touches_d1(store, monkeypatch):
    d = mailstore.create_draft("k1", "answer")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setitem(sys.modules, "mail_tasks", None)  # importing it would raise
    with pytest.raises(SystemExit, match="TTY|terminal"):
        mailstore.approve(d["draft_id"])
    assert mailstore.list_drafts()[0]["draft_id"] == d["draft_id"]


def test_approve_fails_closed_with_no_verifier(store, fake_mailbot, monkeypatch):
    d = mailstore.create_draft("k1", "answer")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    asked = []
    with pytest.raises(SystemExit, match="no approval passphrase is set"):
        mailstore.approve(d["draft_id"], _getpass=lambda p: asked.append(p) or PASS)
    assert asked == [] and not fake_mailbot.exists() and _draft_status(d["draft_id"]) == "draft"


def test_approve_needs_the_passphrase_not_the_draft_id(store, fake_mailbot, monkeypatch):
    mailstore._write_verifier(PASS)
    assert os.stat(mailstore.VERIFIER).st_mode & 0o777 == 0o600
    assert PASS not in Path(mailstore.VERIFIER).read_text()
    d = mailstore.create_draft("k1", "Roof quote: 4k")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    for guess in (d["draft_id"], "", PASS.upper()):  # the old typed-back confirmation, empty, near miss
        with pytest.raises(SystemExit, match="wrong passphrase"):
            mailstore.approve(d["draft_id"], _getpass=lambda p, g=guess: g)
    assert not fake_mailbot.exists() and _draft_status(d["draft_id"]) == "draft"
    mailstore.approve(d["draft_id"], _getpass=lambda p: PASS)
    assert fake_mailbot.read_text() == "k1|Roof quote: 4k\n" and _draft_status(d["draft_id"]) == "approved"


def test_body_swapped_after_display_is_not_what_is_sent(store, fake_mailbot, monkeypatch):
    mailstore._write_verifier(PASS)
    d = mailstore.create_draft("k1", "what Frank reviewed")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def swap_then_type(prompt):
        c = sqlite3.connect(mailstore.DRAFTS_DB)
        c.execute("UPDATE drafts SET body='attacker text' WHERE id=?", (d["draft_id"],))
        c.commit()
        return PASS
    mailstore.approve(d["draft_id"], _getpass=swap_then_type)
    assert fake_mailbot.read_text() == "k1|what Frank reviewed\n"


def test_set_passphrase_never_silently_overwrites(store, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["short", "short"])
    with pytest.raises(SystemExit, match="at least"):
        mailstore.set_passphrase(_getpass=lambda p: next(answers))
    answers = iter([PASS, PASS])
    mailstore.set_passphrase(_getpass=lambda p: next(answers))
    first = Path(mailstore.VERIFIER).read_text()
    answers = iter(["not the current one"])
    with pytest.raises(SystemExit, match="wrong passphrase"):
        mailstore.set_passphrase(_getpass=lambda p: next(answers))
    assert Path(mailstore.VERIFIER).read_text() == first
    answers = iter([PASS, "a brand new passphrase", "a brand new passphrase"])
    mailstore.set_passphrase(_getpass=lambda p: next(answers))
    assert mailstore._passphrase_ok(mailstore._load_verifier(), "a brand new passphrase")


def test_verifier_path_ignores_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("MAILBOT_APPROVE_VERIFIER", "/tmp/attacker.json")
    monkeypatch.setenv("MAILBOT_VERIFIER", "/tmp/attacker.json")
    fresh = importlib.reload(mailstore)
    try:
        assert fresh.VERIFIER == "/home/lumen/.config/mailbot-approve/verifier.json"
    finally:
        monkeypatch.delenv("MAILBOT_APPROVE_VERIFIER"); monkeypatch.delenv("MAILBOT_VERIFIER")
        importlib.reload(mailstore)


def test_drafts_db_made_0644_by_someone_else_is_tightened(store):
    Path(mailstore.DRAFTS_DB).touch(mode=0o644)
    os.chmod(mailstore.DRAFTS_DB, 0o644)
    mailstore.list_drafts()
    assert os.stat(mailstore.DRAFTS_DB).st_mode & 0o777 == 0o600


SCRIPT = shutil.which("script")


def _drive_script(store, fake_dir, draft_id, answer, timeout=60):
    """Run approve the way the wq#5094 probe did, `script -qec "..." /dev/null`, and type `answer`
    only after the prompt shows. Pre-fed input would be dropped by getpass's TCSAFLUSH and
    pass for the wrong reason; an agent driving a pty can wait for the prompt too."""
    prelude = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import mailstore as m; "
               f"m.MAIL_DB={mailstore.MAIL_DB!r}; m.DRAFTS_DB={mailstore.DRAFTS_DB!r}; "
               f"m.VERIFIER={mailstore.VERIFIER!r}; m.MAILBOT={fake_dir!r}; m.FAIL_DELAY=0; "
               f"m._cli(['approve', {draft_id!r}])")
    inner = f"{shlex.quote(sys.executable)} -c {shlex.quote(prelude)}"
    proc = subprocess.Popen([SCRIPT, "-qec", inner, "/dev/null"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    out, sent, deadline = b"", False, time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        r, _, _ = select.select([proc.stdout], [], [], 0.2)
        if r:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            out += chunk
            if not sent and b"passphrase" in out:
                time.sleep(0.3)  # let getpass finish its tcsetattr before typing
                proc.stdin.write(answer.encode() + b"\n"); proc.stdin.flush()
                sent = True
    else:
        proc.kill()
        raise AssertionError(f"script(1) run timed out: {out!r}")
    out += proc.stdout.read() or b""
    proc.wait(10)
    return proc.returncode, out.decode(errors="replace"), sent


@pytest.mark.skipif(not SCRIPT, reason="util-linux script(1) not installed")
def test_script1_pty_bypass_is_refused(store, fake_mailbot):
    """wq#5175 acceptance: the wq#5094 bypass (script(1) pty + typing the draft id back) sends nothing."""
    mailstore._write_verifier(PASS)
    d = mailstore.create_draft("k1", "Roof quote: 4k")
    fake_dir = mailstore.MAILBOT
    for attack in (d["draft_id"], "y", "approve"):
        rc, out, sent = _drive_script(store, fake_dir, d["draft_id"], attack)
        assert sent, f"harness never reached the passphrase prompt: {out!r}"
        assert rc != 0 and "wrong passphrase, nothing sent" in out, out
        assert not fake_mailbot.exists() and _draft_status(d["draft_id"]) == "draft"
    # positive control: the same harness with the real passphrase does send, so the refusals above
    # came from the passphrase check and not from a harness that never reached the gate
    rc, out, sent = _drive_script(store, fake_dir, d["draft_id"], PASS)
    assert rc == 0 and "queued" in out, out
    assert fake_mailbot.read_text() == "k1|Roof quote: 4k\n" and _draft_status(d["draft_id"]) == "approved"


def test_no_token_file_is_opened(store, monkeypatch):
    seen = []
    real_open, real_os_open = builtins.open, os.open
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: (seen.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (seen.append(str(p)), real_os_open(p, *a, **k))[1])
    mailstore.mirror_status(); mailstore.search("roof"); mailstore.thread("t1"); mailstore.contact("ann")
    mailstore.needs_frank(); mailstore.pending_tasks(); mailstore.create_draft("k1", "a"); mailstore.list_drafts()
    assert seen, "probe saw no opens at all; it cannot detect a token read"
    assert not [p for p in seen if re.search(r"\.cf_|token|secret", os.path.basename(p), re.I)]


def test_tool_surface_has_no_send_and_matches_launcher_allowlist():
    import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"mail_status", "mail_search", "mail_thread", "mail_contact", "mail_needs_frank",
                     "mail_pending_tasks", "mail_draft_create", "mail_drafts"}
    assert not [n for n in names if re.search(r"send|delete|forward|calendar|approve", n)]
    allow = re.search(r"--allow (\S+)", (HERE / "launcher.sh").read_text()).group(1)
    assert set(allow.split(",")) == names
