#!/usr/bin/env python3
"""gate_store.py — the dispatch_gate parking table (DISPATCH_REBUILD_SPEC step 11, wq#2134).

A ticket that fails a gate for a reason only an edit or the clock can change was re-derived
every 5 minutes: ~120 `wq show` + lint runs per pass for ~120 tickets nobody could dispatch
(DISPATCH_DISSECTION F8). The pass now PARKS such a ticket in `dispatch_gate` and the scan
skips it until either

  * its fingerprint changes  — hash of the ticket columns the failing gate read, plus lane,
    title and detail (rewriting a ticket is the documented fix for every terminal skip, so a
    rewrite always re-opens it), plus the status of each dep for a deps_unmet park (a dep going
    `done` is not an edit of the ticket), plus the dispatcher config sha (the caller's salt), or
  * `recheck_after` passes    — lane_cap: next UTC midnight; held: the hold's expiry; terminal:
    6 h safety flush (config/policy edits the fingerprint cannot see); anything else: 1 h.

The table lives in the wq sqlite db next to `tasks` (create-if-missing, WAL, every call its own
short transaction, 5 s busy timeout). FAIL-OPEN, always: any sqlite/OS error reads as "not
parked", so a broken store degrades to the pre-step-11 behaviour (evaluate everything) and can
never hide a ticket. FOREMAN_GATE_STORE=off turns parking off entirely (rollback lever,
DISPATCH_REBUILD_SPEC §5 item 8); the janitor's inputs (`parked_rows`, `rows_by_reason`) read
empty, while the read-only status views (`by_reason`, `due`) still work.
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_DB = "/home/lumen/agents/work-queue/queue.db"
BUSY_TIMEOUT_S = 5
TERMINAL_FLUSH_H = 6
OPEN_RECHECK_H = 1
CORE_INPUTS = ("lane", "title", "detail")
TS_FMT = "%Y-%m-%d %H:%M:%S"  # wq's own naive-UTC idiom: lexical compare == time compare

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_gate(
    ticket        INTEGER PRIMARY KEY,
    reason        TEXT NOT NULL,
    lane          TEXT,
    fingerprint   TEXT NOT NULL,
    inputs        TEXT NOT NULL DEFAULT '[]',
    terminal      INTEGER NOT NULL DEFAULT 0,
    parked_at     TEXT NOT NULL,
    recheck_after TEXT NOT NULL,
    detail        TEXT
)"""

_ready = set()  # db paths whose schema this process has already ensured


def enabled():
    return os.environ.get("FOREMAN_GATE_STORE", "").strip().lower() != "off"


def db_path():
    return os.environ.get("WQ_DB") or DEFAULT_DB  # the same contract bin/wq and self_heal.wq_rows use


def _utcnow():
    return datetime.now(timezone.utc)


def _fmt(dt):
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _coerce_ts(v):
    """ISO string (Z / offset / naive-UTC, `T` or space) or datetime -> TS_FMT; None if unparseable."""
    if isinstance(v, datetime):
        return _fmt(v if v.tzinfo else v.replace(tzinfo=timezone.utc))
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return _fmt(d if d.tzinfo else d.replace(tzinfo=timezone.utc))


def _connect(write):
    """Readers open the db read-only and never create the table (the dashboard is read-only by
    construction; a missing table just reads as empty). Writers create-if-missing, WAL."""
    path = db_path()
    if write:
        con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    else:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S,
                              isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_S * 1000}")
    if write and path not in _ready:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(SCHEMA)
        _ready.add(path)
    return con


def _with_con(fn, default, write=False):
    """Run fn(con) on a fresh short-lived connection; any sqlite/OS error -> `default`."""
    try:
        con = _connect(write)
    except (sqlite3.Error, OSError):
        _ready.discard(db_path())
        return default
    try:
        return fn(con)
    except (sqlite3.Error, OSError):
        if write:
            _ready.discard(db_path())  # re-ensure the schema next time (db swapped / table dropped)
        return default
    finally:
        con.close()


# ---- fingerprints ---------------------------------------------------------------------

def _ticket_rows(con, ids):
    ids = [int(i) for i in ids]
    out = {}
    for i in range(0, len(ids), 500):  # SQLITE_MAX_VARIABLE_NUMBER headroom
        chunk = ids[i:i + 500]
        q = f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(chunk))})"
        for row in con.execute(q, chunk):
            out[int(row["id"])] = dict(row)
    return out


def _dep_statuses(con, deps):
    ids = [int(d) for d in str(deps or "").split(",") if d.strip().isdigit()]
    have = {i: r.get("status") for i, r in _ticket_rows(con, ids).items()}
    return sorted((i, have.get(i, "missing")) for i in ids)


def _fingerprint(con, row, inputs, salt):
    parts = [salt or ""]
    for col in sorted(set(inputs) | set(CORE_INPUTS)):
        parts.append((col, row.get(col)))
        if col == "deps":
            parts.append(("deps_status", _dep_statuses(con, row.get("deps"))))
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint(ticket, inputs=(), salt="", row=None):
    """Hash of `inputs` (+ CORE_INPUTS) of the ticket as it is NOW — or of `row`, the caller's
    own snapshot of it (`wq show --json` carries the sqlite column names and types) — or None
    when the row cannot be read (missing ticket, sqlite error): callers treat None as "do not
    park". A dep's status is always read live."""
    def go(con):
        r = row or _ticket_rows(con, [ticket]).get(int(ticket))
        return _fingerprint(con, r, inputs, salt) if r else None
    return _with_con(go, None)


# ---- API ------------------------------------------------------------------------------

def default_recheck(terminal, now=None):
    now = now or _utcnow()
    return now + timedelta(hours=TERMINAL_FLUSH_H if terminal else OPEN_RECHECK_H)


def park(ticket, reason, fingerprint, *, inputs=(), lane=None, terminal=False,
         recheck_after=None, detail=None, now=None):
    """Upsert the park row. Returns the row's `recheck_after` (TS_FMT) or None when nothing was
    written (store off, sqlite error). `recheck_after` None takes the terminal / open default."""
    if not enabled() or fingerprint is None:
        return None
    now = now or _utcnow()
    recheck = _coerce_ts(recheck_after) or _fmt(default_recheck(terminal, now))
    if recheck <= _fmt(now):  # a natural recheck already in the past would park nothing
        recheck = _fmt(default_recheck(terminal, now))

    def go(con):
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "INSERT INTO dispatch_gate(ticket, reason, lane, fingerprint, inputs, terminal, "
                "parked_at, recheck_after, detail) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ticket) DO UPDATE SET reason=excluded.reason, lane=excluded.lane, "
                "fingerprint=excluded.fingerprint, inputs=excluded.inputs, "
                "terminal=excluded.terminal, parked_at=excluded.parked_at, "
                "recheck_after=excluded.recheck_after, detail=excluded.detail",
                (int(ticket), reason, lane, fingerprint, json.dumps(sorted(inputs)),
                 1 if terminal else 0, _fmt(now), recheck,
                 None if detail is None else str(detail)[:500]))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        return recheck
    return _with_con(go, None, write=True)


def unpark(ticket):
    """Delete the ticket's park row. True iff a row was removed."""
    def go(con):
        return con.execute("DELETE FROM dispatch_gate WHERE ticket=?", (int(ticket),)).rowcount > 0
    return _with_con(go, False, write=True)


def unpark_many(tickets):
    tickets = [int(t) for t in tickets]
    def go(con):
        n = 0
        for i in range(0, len(tickets), 500):
            chunk = tickets[i:i + 500]
            n += con.execute(
                f"DELETE FROM dispatch_gate WHERE ticket IN ({','.join('?' * len(chunk))})",
                chunk).rowcount
        return n
    return _with_con(go, 0, write=True) if tickets else 0


def is_parked(ticket, fingerprint, now=None):
    """True iff the ticket has a park row whose fingerprint still matches and whose
    recheck_after has not passed. Fail-open: store off / any error / no row -> False."""
    if not enabled() or fingerprint is None:
        return False
    stamp = _fmt(now or _utcnow())
    def go(con):
        row = con.execute("SELECT fingerprint, recheck_after FROM dispatch_gate WHERE ticket=?",
                          (int(ticket),)).fetchone()
        return bool(row and row["fingerprint"] == fingerprint and row["recheck_after"] > stamp)
    return _with_con(go, False)


def parked_rows(ids=None):
    """{ticket: row dict} of every park row (or the given ids). {} on error or store off."""
    ids = None if ids is None else [int(i) for i in ids]
    if not enabled() or (ids is not None and not ids):
        return {}

    def go(con):
        q, args = "SELECT * FROM dispatch_gate", []
        if ids is not None:
            q += f" WHERE ticket IN ({','.join('?' * len(ids))})"
            args = ids
        return {int(r["ticket"]): dict(r) for r in con.execute(q, args)}
    return _with_con(go, {})


def due(now=None):
    """Park rows whose recheck_after has passed — the ones the next pass re-evaluates by time."""
    stamp = _fmt(now or _utcnow())
    def go(con):
        return [dict(r) for r in con.execute(
            "SELECT * FROM dispatch_gate WHERE recheck_after<=? ORDER BY recheck_after", (stamp,))]
    return _with_con(go, [])


def by_reason():
    """{reason: count} over parked tickets that are still `open` (a row outlives its ticket
    until the janitor purges it; counting it would over-report). {} on error."""
    def go(con):
        return {r["reason"]: r["n"] for r in con.execute(
            "SELECT g.reason AS reason, COUNT(*) AS n FROM dispatch_gate g "
            "JOIN tasks t ON t.id=g.ticket WHERE t.status='open' "
            "GROUP BY g.reason ORDER BY n DESC, g.reason")}
    return _with_con(go, {})


def rows_by_reason():
    """{reason: [row dict + title]} for parked open tickets — the janitor's digest input.
    {} on error or store off (rows left from before the switch must not be acted on)."""
    if not enabled():
        return {}

    def go(con):
        out = {}
        for r in con.execute(
                "SELECT g.*, t.title AS title FROM dispatch_gate g JOIN tasks t ON t.id=g.ticket "
                "WHERE t.status='open' ORDER BY g.reason, g.ticket"):
            out.setdefault(r["reason"], []).append(dict(r))
        return out
    return _with_con(go, {})


def purge_closed():
    """Drop rows whose ticket is no longer `open` (done / blocked / claimed / deleted)."""
    def go(con):
        return con.execute("DELETE FROM dispatch_gate WHERE ticket NOT IN "
                           "(SELECT id FROM tasks WHERE status='open')").rowcount
    return _with_con(go, 0, write=True)


def partition(cands, salt="", now=None):
    """Split scan rows into (evaluate, parked, released) against the store, in one read.

    `parked` are candidates the pass skips outright. `released` are (cand, why, row) for a park
    row that no longer holds — `why` is recheck_due | fingerprint_changed | deps_changed |
    ticket_missing — the caller deletes the row and logs the unpark; the candidate is in
    `evaluate`. Pure read; FAIL-OPEN: store off or any error -> everything is evaluated."""
    if not enabled() or not cands:
        return list(cands), [], []
    stamp = _fmt(now or _utcnow())

    def go(con):
        rows = {int(r["ticket"]): dict(r) for r in con.execute("SELECT * FROM dispatch_gate")}
        hits = [c for c in cands if c["id"] in rows]
        if not hits:
            return list(cands), [], []
        tickets = _ticket_rows(con, [c["id"] for c in hits])
        evaluate, parked, released = [], [], []
        for c in cands:
            row = rows.get(c["id"])
            if row is None:
                evaluate.append(c)
                continue
            t = tickets.get(c["id"])
            if t is None:
                released.append((c, "ticket_missing", row))
                evaluate.append(c)
                continue
            try:
                inputs = json.loads(row["inputs"] or "[]")
            except ValueError:
                inputs = []
            if row["recheck_after"] <= stamp:
                why = "recheck_due"
            elif _fingerprint(con, t, inputs, salt) != row["fingerprint"]:
                why = "deps_changed" if row["reason"] == "deps_unmet" else "fingerprint_changed"
            else:
                parked.append((c, row))
                continue
            released.append((c, why, row))
            evaluate.append(c)
        return evaluate, parked, released
    return _with_con(go, (list(cands), [], []))


def main(argv=None):
    """`gate_store.py status` — parked counts by reason (open tickets) and due rows."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["status"]:
        print("usage: gate_store.py status", file=sys.stderr)
        return 2
    print(json.dumps({"enabled": enabled(), "db": db_path(), "by_reason": by_reason(),
                      "due": len(due())}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
