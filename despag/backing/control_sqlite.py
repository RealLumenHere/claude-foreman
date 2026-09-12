"""wq#1807: despag P1-4 — local control-plane store.

Mirrors the SEMANTICS of research/harnessrouter-ref/src/gateway/control_sqlite.py +
control_store.py's idem_reserve/lease_admit/resp_mark_terminal, not their code: HR
splits a Cosmos-parity container (control_sqlite.py, ~230 ln) from the business logic
that rides on it (control_store.py) because it has to run either backing unmodified.
despag is local-only (C.4), so there is nothing to keep byte-identical across two
backends — one file, same three guarantees, no org/partition-key plumbing (Lumen is
single-tenant, security.md "single-tenant subset").

Every write here happens inside ONE `BEGIN IMMEDIATE` transaction: SQLite grants that
lock to a single writer at a time (others block behind `busy_timeout` until it
COMMITs/ROLLBACKs), which is what HR's separate etag-CAS exists to emulate over
Cosmos's read-then-replace-with-etag API. One transaction per op makes the read and
the write atomic by construction, so there is no etag to check.

Three guarantees, matching PHASE0_WIRING_SPEC.md C.4 / E.5:
  idempotency reserve — atomic INSERT; the loser reads the WINNER's resp_id and
    replays it instead of executing again (this is the dispatcher double-spawn fix,
    E.5, generalised: at most one reservation ever exists per key). Retention is
    ttl_s (callers pass >=24h, per errors.md's "Idempotency-Key ... >=24h retention").
    FAIL-CLOSED: any sqlite error propagates so the caller can refuse (503) rather
    than risk a second spawn.
  session lease + monotonic fence — a turn "holds" a session by bumping `fence`;
    a later turn's admit bumps it again, so an earlier turn's own fence is stale the
    instant it no longer matches, and lease_still_held() is the gate a turn checks
    before it commits. BEST-EFFORT: any error fails OPEN (unfenced) rather than wedge
    every turn on a control-store outage.
  response terminal latch — resp_mark_terminal() is one-way: once a response is
    cancelled/completed/failed/incomplete, no later write (including a racing
    resp_put_running) can move it back to "running" or to a different terminal
    status. Idempotent: re-marking terminal just returns the existing record.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path

_TERMINAL = {"cancelled", "completed", "failed", "incomplete"}


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("CREATE TABLE IF NOT EXISTS control ("
                "id TEXT PRIMARY KEY, body TEXT NOT NULL, etag INTEGER NOT NULL, expires REAL)")
    return con


def _live(con: sqlite3.Connection, item_id: str):
    """Fetch a row, treating an expired document as absent (sweeping it). TTL here is
    lazy, never late: nothing reads a document as live past its expiry."""
    row = con.execute("SELECT body, etag, expires FROM control WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return None
    if row[2] is not None and row[2] <= time.time():
        con.execute("DELETE FROM control WHERE id=?", (item_id,))
        return None
    return row


def _put(con: sqlite3.Connection, item_id: str, body: dict, expires: float) -> None:
    con.execute(
        "INSERT INTO control(id, body, etag, expires) VALUES (?,?,1,?) "
        "ON CONFLICT(id) DO UPDATE SET body=excluded.body, etag=control.etag+1, expires=excluded.expires",
        (item_id, json.dumps(body), expires),
    )


class ControlStore:
    def __init__(self, path: str):
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _connect(path).close()          # create schema up front, surface I/O errors early

    # ── idempotency reserve ────────────────────────────────────────────────
    async def idem_reserve(self, key: str, resp_id: str, ttl_s: int = 86400) -> dict | None:
        """None => we won the race, caller executes. dict => someone else already
        reserved `key` first; it is THEIR record (read resp_id, replay their result).
        Fail-closed: a sqlite error propagates."""
        item_id = f"idem::{key}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, item_id)
            if row is not None:
                con.execute("COMMIT")
                return json.loads(row[0])
            body = {"resp_id": resp_id, "state": "reserved", "ts": time.time()}
            _put(con, item_id, body, time.time() + int(ttl_s))
            con.execute("COMMIT")
            return None
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    async def idem_get(self, key: str) -> dict | None:
        con = _connect(self._path)
        try:
            row = _live(con, f"idem::{key}")
            return json.loads(row[0]) if row else None
        finally:
            con.close()

    async def idem_settle(self, key: str, resp_id: str, output: dict, ttl_s: int = 86400) -> None:
        """Attach the winner's finished output to its own reservation so a racer that
        reads it gets a real result instead of state=reserved forever. Only settles
        OUR reservation — never overwrites one a retry already reused."""
        item_id = f"idem::{key}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, item_id)
            cur = json.loads(row[0]) if row else None
            if cur is None or cur.get("resp_id") != resp_id:
                con.execute("ROLLBACK")
                return
            _put(con, item_id, {**cur, "state": "done", "output": output}, time.time() + int(ttl_s))
            con.execute("COMMIT")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
        finally:
            con.close()

    async def idem_release(self, key: str, resp_id: str) -> None:
        """Best-effort release of a reservation whose request failed BEFORE it settled
        any output — otherwise a retry would replay a resp_id that never produced a
        result. Only deletes the item if it still points at OUR resp_id and is still
        `reserved` (never clobber a settled record, or one a retry already reused)."""
        item_id = f"idem::{key}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, item_id)
            cur = json.loads(row[0]) if row else None
            if cur is not None and cur.get("resp_id") == resp_id and cur.get("state") == "reserved":
                con.execute("DELETE FROM control WHERE id=?", (item_id,))
            con.execute("COMMIT")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
        finally:
            con.close()

    # ── per-session lease with monotonic fence ─────────────────────────────
    async def lease_admit(self, sid: str, owner: str, ttl_s: int, *,
                          reject_on_conflict: bool = False, fresh_s: float = 75.0) -> dict:
        """Admission gate at turn start. A different owner counts as an ACTIVE
        conflict only if its lease is live (expires in the future) AND fresh
        (heartbeated within fresh_s) — a crashed lease that stopped renewing does not
        falsely reject a legitimate follow-up.

        reject_on_conflict=False (observe): always take ownership (bump fence), just
          report the conflict.
        reject_on_conflict=True (enforce): on an active conflict, leave the lease
          untouched (never steal the running turn's fence) and return rejected=True.

        Best-effort: any store error fails OPEN (fence=0, unfenced turn proceeds)."""
        doc_id = f"lease::{sid}"
        now = time.time()
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, doc_id)
            cur = json.loads(row[0]) if row else {}
            held_by = str(cur.get("owner") or "")
            live = float(cur.get("expires") or 0) > now
            fresh = (now - float(cur.get("ts") or 0)) < fresh_s
            other = bool(held_by and held_by != owner)
            conflict = bool(live and other)
            if reject_on_conflict and conflict and fresh:
                con.execute("COMMIT")
                return {"fence": 0, "conflict": True, "rejected": True, "holder": held_by}
            fence = int(cur.get("fence", 0)) + 1
            _put(con, doc_id, {"fence": fence, "owner": owner, "expires": now + ttl_s, "ts": now},
                 now + max(int(ttl_s) * 4, 3600))
            con.execute("COMMIT")
            return {"fence": fence, "conflict": conflict, "rejected": False, "holder": held_by}
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
            return {"fence": 0, "conflict": False, "rejected": False, "holder": ""}
        finally:
            con.close()

    async def lease_renew(self, sid: str, owner: str, fence: int, ttl_s: int) -> bool:
        """Heartbeat: extend expiry while we still hold `fence`. Best-effort."""
        if fence <= 0:
            return False
        doc_id = f"lease::{sid}"
        now = time.time()
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, doc_id)
            cur = json.loads(row[0]) if row else None
            if cur is None or int(cur.get("fence", 0)) != fence or str(cur.get("owner") or "") != owner:
                con.execute("ROLLBACK")
                return False
            _put(con, doc_id, {**cur, "expires": now + ttl_s, "ts": now}, now + max(int(ttl_s) * 4, 3600))
            con.execute("COMMIT")
            return True
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
            return False
        finally:
            con.close()

    async def lease_release(self, sid: str, owner: str, fence: int) -> None:
        """Turn end: expire the lease now if we still hold `fence`, so a follow-up
        turn admits without waiting out the TTL. Best-effort; never raises."""
        if fence <= 0:
            return
        doc_id = f"lease::{sid}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, doc_id)
            cur = json.loads(row[0]) if row else None
            if cur is None or int(cur.get("fence", 0)) != fence or str(cur.get("owner") or "") != owner:
                con.execute("ROLLBACK")
                return
            _put(con, doc_id, {**cur, "expires": 0, "ts": time.time()}, time.time())
            con.execute("COMMIT")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
        finally:
            con.close()

    async def lease_still_held(self, sid: str, fence: int) -> bool:
        """True iff the fence is unchanged since this turn acquired it — the gate a
        turn checks before it commits (a stale fence refuses the commit). Fail-safe:
        an unreadable store returns True rather than silently drop the write."""
        if fence <= 0:
            return True
        con = _connect(self._path)
        try:
            row = _live(con, f"lease::{sid}")
            return bool(row) and json.loads(row[0]).get("fence") == fence
        except sqlite3.Error:
            return True
        finally:
            con.close()

    # ── response terminal latch ────────────────────────────────────────────
    async def resp_put_running(self, rid: str, sid: str, ttl_s: int) -> None:
        """Upsert as live. Never revives a terminal record (monotonic latch)."""
        doc_id = f"resp::{rid}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, doc_id)
            cur = json.loads(row[0]) if row else {}
            if cur.get("status") in _TERMINAL:
                con.execute("COMMIT")
                return
            _put(con, doc_id, {**cur, "status": "running", "sid": sid, "ts": time.time()},
                 time.time() + int(ttl_s))
            con.execute("COMMIT")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
        finally:
            con.close()

    async def resp_mark_terminal(self, rid: str, status: str, ttl_s: int) -> dict:
        """One-way transition to a terminal status. Idempotent: an already-terminal
        record is returned unchanged, even for a different `status` — the FIRST
        terminal write wins and nothing later can move it, forward or back."""
        if status not in _TERMINAL:
            raise ValueError(f"not a terminal status: {status!r}")
        doc_id = f"resp::{rid}"
        con = _connect(self._path)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = _live(con, doc_id)
            cur = json.loads(row[0]) if row else {}
            if cur.get("status") in _TERMINAL:
                con.execute("COMMIT")
                return cur
            body = {**cur, "status": status, "ts": time.time()}
            _put(con, doc_id, body, time.time() + int(ttl_s))
            con.execute("COMMIT")
            return body
        finally:
            con.close()

    async def resp_get(self, rid: str) -> dict | None:
        con = _connect(self._path)
        try:
            row = _live(con, f"resp::{rid}")
            return json.loads(row[0]) if row else None
        finally:
            con.close()
