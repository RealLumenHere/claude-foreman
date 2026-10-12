#!/usr/bin/env python3
"""ledger — research rejection ledger in queue.db (wq#3760, design/wq3755 §5).

One row per proposal Frank rejected. It is read back two ways: the trigger prepends the
last 50 rows to every brief as a constraint block, and a proposal whose title or sha256
matches a row is refused before sandbox spend (sandbox_gate.drain) and again at the bridge.

The table is additive, created on first use like every other wq table (CREATE TABLE IF NOT
EXISTS) — no migration script, nothing else in queue.db changes.
"""
import datetime
import os
import re
import sqlite3

DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
FMT = "%Y-%m-%d %H:%M:%S"  # wq's own timestamp format (bin/wq FMT)
CONSTRAINT_ROWS = 50
CLASS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def connect(db=None):
    c = sqlite3.connect(db or DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    ensure_table(c)
    return c


def ensure_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS rejections(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        proposal_sha256 TEXT NOT NULL, title TEXT NOT NULL, reject_class TEXT NOT NULL,
        frank_reason TEXT NOT NULL, brief_id TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rejections_sha ON rejections(proposal_sha256)")
    c.commit()


def norm_title(title):
    return " ".join(str(title or "").split()).casefold()


def record(c, proposal_sha256, title, reject_class, frank_reason, brief_id=None, now=None):
    """Write one ledger row; returns its id. Rejecting the same sha twice keeps the first."""
    if not CLASS_RE.match(reject_class or ""):
        raise ValueError(f"reject_class must match {CLASS_RE.pattern}")
    reason = " ".join(str(frank_reason or "").split())
    if not reason:
        raise ValueError("frank_reason is required")
    row = c.execute("SELECT id FROM rejections WHERE proposal_sha256=?",
                    (proposal_sha256,)).fetchone()
    if row:
        return row[0]
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime(FMT)
    cur = c.execute("INSERT INTO rejections(ts, proposal_sha256, title, reject_class, "
                    "frank_reason, brief_id) VALUES(?,?,?,?,?,?)",
                    (ts, proposal_sha256, " ".join(str(title).split()), reject_class,
                     reason, brief_id))
    c.commit()
    return cur.lastrowid


def hit(c, title, proposal_sha256):
    """The ledger row this proposal repeats (same sha or same normalised title), or None."""
    want = norm_title(title)
    for rid, sha, t in c.execute("SELECT id, proposal_sha256, title FROM rejections "
                                 "ORDER BY id DESC"):
        if sha == proposal_sha256 or (want and norm_title(t) == want):
            return rid
    return None


def constraint_block(c, n=CONSTRAINT_ROWS):
    """The brief's constraint block (design §5): newest first, one line per rejection."""
    rows = c.execute("SELECT title, reject_class, frank_reason FROM rejections "
                     "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return "\n".join(f"DO NOT re-propose: {t} ({k}): {r}" for t, k, r in rows)
