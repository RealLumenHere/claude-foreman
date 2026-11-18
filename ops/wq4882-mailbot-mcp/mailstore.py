#!/usr/bin/env python3
"""mailstore — read side of the mailbot MCP adapter, plus the drafts store (wq#4882).

Reads /home/lumen/mailbot/mail.db, the local mirror lumen-mail-sync keeps of the
lumen-mail D1 database (mailbot/mail_recall.py sync, mailbot/mail_tasks.py sync).
mail.db is opened read-only (sqlite URI mode=ro), so nothing here can change it.

Drafts live in their own file, drafts.db (0600), never in mail.db and never in D1.
A draft answers one pending mailbot task. Mailbot has exactly one owner-initiated
send path, the worker cron delivering tasks with status='ready' (src/worker.js
deliverReadyTasks), so a draft only leaves Lumen when a human runs

    python3 mailstore.py approve <draft_id>

from a terminal. That writes the draft through mail_tasks.answer() (D1-Edit token)
and the cron sends it within 15 min. approve refuses without a TTY on stdin, so a
headless agent's Bash call cannot run it.

This module never opens a token file. The MCP server built on it holds no
credential at all: Cloudflare tokens stay with the sync timer and the approve CLI.

  python3 mailstore.py drafts              list drafts awaiting approval
  python3 mailstore.py approve <draft_id>  send one (asks for confirmation on the TTY)
  python3 mailstore.py discard <draft_id>  drop one
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAILBOT = "/home/lumen/mailbot"
MAIL_DB = os.environ.get("MAILBOT_DB", f"{MAILBOT}/mail.db")
DRAFTS_DB = os.environ.get("MAILBOT_DRAFTS_DB", f"{MAILBOT}/drafts.db")
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_LIMIT = 50
BODY_CHARS = 600
MAX_DRAFT_CHARS = 8000
# mail_recall.py:101 cuts the same marker: the worker appends quoted history below it.
QUOTE_MARKER = "- - - - -"

DRAFTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, body TEXT NOT NULL,
  author TEXT, created_at TEXT, status TEXT NOT NULL DEFAULT 'draft', decided_at TEXT);
"""


def _mail() -> sqlite3.Connection:
    if not os.path.exists(MAIL_DB):
        raise FileNotFoundError(f"mail.db mirror missing: {MAIL_DB} (run mail_recall.py sync)")
    c = sqlite3.connect(f"file:{MAIL_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _drafts() -> sqlite3.Connection:
    fresh = not os.path.exists(DRAFTS_DB)
    if fresh:
        os.close(os.open(DRAFTS_DB, os.O_CREAT | os.O_WRONLY, 0o600))
    c = sqlite3.connect(DRAFTS_DB)
    c.row_factory = sqlite3.Row
    c.executescript(DRAFTS_SCHEMA)
    return c


def _pacific(ts: str | None) -> str | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%a %m/%d %I:%M %p")


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))).isoformat()


def _message(r: sqlite3.Row, body_chars: int = BODY_CHARS) -> dict:
    body = (r["body"] or "").split(QUOTE_MARKER)[0].strip()
    return {
        "id": r["id"], "thread_id": r["thread_id"], "when": _pacific(r["ts"]),
        "direction": r["direction"], "from": r["from_addr"], "to": r["to_addr"],
        "probe": bool(r["probe"]), "needs_frank": bool(r["needs_frank"]),
        "body": body[:body_chars], "truncated": len(body) > body_chars,
    }


def mirror_status() -> dict:
    c = _mail()
    newest, n = c.execute("SELECT max(ts), count(*) FROM messages").fetchone()
    pending = c.execute("SELECT count(*) FROM tasks WHERE status='pending'").fetchone()[0]
    return {"messages": n, "newest_message": _pacific(newest), "pending_tasks": pending,
            "note": "local mirror of D1; only as fresh as the last lumen-mail-sync run"}


def search(query: str = "", contact: str = "", since_days: int = 30, limit: int = 20) -> list[dict]:
    """Messages newest-first matching query (body or subject) and contact (name or address)."""
    sql = ["SELECT m.*, t.contact_name, t.contact_email, t.subject FROM messages m JOIN threads t USING(thread_id)",
           "WHERE m.ts >= ?"]
    args: list = [_since(since_days)]
    if query:
        sql.append("AND (lower(m.body) LIKE ? OR lower(t.subject) LIKE ?)")
        args += [f"%{query.lower()}%"] * 2
    if contact:
        sql.append("AND (lower(t.contact_email) LIKE ? OR lower(t.contact_name) LIKE ?)")
        args += [f"%{contact.lower()}%"] * 2
    sql.append("ORDER BY m.ts DESC LIMIT ?")
    args.append(_clamp(limit))
    rows = _mail().execute(" ".join(sql), args).fetchall()
    return [{**_message(r), "contact": r["contact_name"] or r["contact_email"],
             "contact_email": r["contact_email"], "subject": r["subject"]} for r in rows]


def thread(thread_id: str, limit: int = 50) -> dict:
    c = _mail()
    t = c.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
    if not t:
        return {"found": False, "thread_id": thread_id}
    rows = c.execute("SELECT * FROM messages WHERE thread_id = ? ORDER BY ts DESC LIMIT ?",
                     (thread_id, _clamp(limit))).fetchall()
    return {"found": True, "thread_id": thread_id, "contact": t["contact_name"] or t["contact_email"],
            "contact_email": t["contact_email"], "subject": t["subject"],
            "first_seen": _pacific(t["first_seen"]), "last_seen": _pacific(t["last_seen"]),
            "messages": [_message(r, 4000) for r in reversed(rows)]}


def contact(who: str) -> dict:
    """Standing note plus thread list for a contact (name or address fragment)."""
    c = _mail()
    like = f"%{who.lower()}%"
    n = c.execute("""SELECT * FROM contact_notes WHERE lower(contact_email) LIKE ? OR lower(contact_name) LIKE ?
        ORDER BY updated_at DESC LIMIT 1""", (like, like)).fetchone()
    threads = c.execute("""SELECT * FROM threads WHERE lower(contact_email) LIKE ? OR lower(contact_name) LIKE ?
        ORDER BY last_seen DESC LIMIT ?""", (like, like, MAX_LIMIT)).fetchall()
    if not n and not threads:
        return {"found": False, "who": who}
    return {"found": True, "who": who,
            "notes": None if not n else {"contact_email": n["contact_email"], "summary": n["summary"],
                                         "updated_at": _pacific(n["updated_at"])},
            "threads": [{"thread_id": t["thread_id"], "contact_email": t["contact_email"],
                         "subject": t["subject"], "last_seen": _pacific(t["last_seen"])} for t in threads]}


def needs_frank(since_days: int = 30) -> list[dict]:
    rows = _mail().execute("""SELECT m.*, t.contact_name, t.contact_email, t.subject FROM messages m
        JOIN threads t USING(thread_id) WHERE m.needs_frank = 1 AND m.ts >= ? ORDER BY m.ts DESC LIMIT ?""",
                           (_since(since_days), MAX_LIMIT)).fetchall()
    return [{**_message(r, 200), "contact": r["contact_name"] or r["contact_email"],
             "contact_email": r["contact_email"], "subject": r["subject"]} for r in rows]


def _approved_tasks() -> set[str]:
    # mail_tasks.py sync only pulls rows by created_at, so a task answered in D1 stays
    # 'pending' in the mirror. Our own approvals are the record that it is no longer open.
    if not os.path.exists(DRAFTS_DB):
        return set()
    return {r[0] for r in _drafts().execute("SELECT task_id FROM drafts WHERE status = 'approved'")}


def pending_tasks() -> list[dict]:
    done = _approved_tasks()
    rows = _mail().execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at").fetchall()
    return [{"task_id": r["id"], "thread_id": r["thread_id"], "when": _pacific(r["created_at"]),
             "contact": r["contact_name"] or r["contact_email"], "contact_email": r["contact_email"],
             "subject": r["subject"], "category": r["category"], "body": (r["body"] or "")[:BODY_CHARS]}
            for r in rows if r["id"] not in done]


def create_draft(task_id: str, body: str, author: str = "mcp") -> dict:
    body = (body or "").strip()
    if not body:
        raise ValueError("draft body is empty")
    if len(body) > MAX_DRAFT_CHARS:
        raise ValueError(f"draft exceeds {MAX_DRAFT_CHARS} chars")
    task = _mail().execute("SELECT id, status, contact_email, subject FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise ValueError(f"no mailbot task {task_id!r} in the mirror; drafts answer a pending task "
                         "(mailbot's only owner-initiated send path)")
    if task["status"] != "pending" or task_id in _approved_tasks():
        raise ValueError(f"task {task_id} is already answered, not pending")
    did = uuid.uuid4().hex[:12]
    c = _drafts()
    c.execute("INSERT INTO drafts (id, task_id, body, author, created_at) VALUES (?,?,?,?,?)",
              (did, task_id, body, author[:64], datetime.now(timezone.utc).isoformat()))
    c.commit()
    return {"draft_id": did, "task_id": task_id, "to": task["contact_email"], "subject": task["subject"],
            "status": "draft", "next": f"Frank approves from a terminal: python3 {os.path.abspath(__file__)} approve {did}"}


def list_drafts(status: str = "draft") -> list[dict]:
    rows = _drafts().execute("SELECT * FROM drafts WHERE status = ? ORDER BY created_at", (status,)).fetchall()
    return [{"draft_id": r["id"], "task_id": r["task_id"], "author": r["author"],
             "created": _pacific(r["created_at"]), "body": r["body"]} for r in rows]


def _decide(draft_id: str, status: str) -> sqlite3.Row:
    c = _drafts()
    r = c.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if not r or r["status"] != "draft":
        raise SystemExit(f"no open draft {draft_id}")
    c.execute("UPDATE drafts SET status = ?, decided_at = ? WHERE id = ?",
              (status, datetime.now(timezone.utc).isoformat(), draft_id))
    c.commit()
    return r


def approve(draft_id: str) -> None:
    if not sys.stdin.isatty():
        raise SystemExit("approve needs a human at a terminal (stdin is not a TTY); "
                         "run it over ssh -t, not from an agent")
    r = _drafts().execute("SELECT * FROM drafts WHERE id = ? AND status = 'draft'", (draft_id,)).fetchone()
    if not r:
        raise SystemExit(f"no open draft {draft_id}")
    print(f"task {r['task_id']}\n---\n{r['body']}\n---")
    if input(f"type {draft_id} to queue this for sending: ").strip() != draft_id:
        raise SystemExit("not confirmed, nothing sent")
    if r["task_id"] in _approved_tasks():
        raise SystemExit(f"task {r['task_id']} already has an approved draft; discard this one")
    sys.path.insert(0, MAILBOT)
    import mail_tasks  # holds the D1-Edit token path; imported only on this human-run path
    mail_tasks.answer(r["task_id"], r["body"])
    _decide(draft_id, "approved")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("drafts")
    sub.add_parser("approve").add_argument("draft_id")
    sub.add_parser("discard").add_argument("draft_id")
    a = ap.parse_args()
    if a.cmd == "drafts":
        print(json.dumps(list_drafts(), indent=2))
    elif a.cmd == "approve":
        approve(a.draft_id)
    else:
        _decide(a.draft_id, "discarded")
        print(f"discarded {a.draft_id}")
