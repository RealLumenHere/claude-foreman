#!/usr/bin/env python3
"""mortician.py — terminal-failure triage over the wq table (wq#2486, the Mortician minus NATS).

research/DIGEST_Fleet_searchability_md.md T6: a task that has exhausted its retries must be
analysed ONCE and the reason recorded, so nothing re-attempts a fundamentally impossible job.
Frank's source doc rides NATS JetStream; this fleet does not run NATS (which nats -> rc=1) and
a sibling digest already rejected a broker from the same premise
(research/DIGEST_Fleet_Memory_3_md.md:52), so this pass REUSES THE WQ TABLE.

What it does, per un-triaged terminally-failed row:
  (1) classify  — freeze one root-cause class into the `morgue` table (INSERT OR IGNORE,
                  UNIQUE ticket_id: the classification never changes after first write)
  (2) record    — write ONE durable lesson file to the minerva/dream proposal sink
                  (proposal-only per instinct 7: nothing here edits claude_memory.md or
                  instincts.yaml; candidates go to minerva/dream for Frank review)
  (3) mark      — append one `[mortician] ... do-not-redispatch` note to the wq row via
                  `wq note` (wq#1331 append-only), so every later reader sees the verdict.
                  dispatch_janitor.release_rung_faults() consults is_triaged() and never
                  auto-releases a row the mortician has closed the book on.

Exactly-once discipline is copied from bin/aq_escalate.py:13-21: UNIQUE key + step-wise
markers (recorded_at / marked_at). A transient failure (unwritable dream dir, wq hiccup)
leaves that step's marker NULL and the next run retries JUST that step — it never
re-classifies and never double-writes a record or a note. A triage pass that re-fires on
every run is a storm generator, which is the defect the source doc's own design has.

Population — rows nothing is going to retry:
  * status='blocked' whose class is NOT a live RUNG_FAULT (janitor heals those), older
    than MORT_GRACE_HOURS so in-flight healing/human triage isn't preempted. A RUNG_FAULT
    row whose heal attempts are exhausted (self_heal.MAX_HEAL_ATTEMPTS) is dead, not live.
  * status in ('rejected','cancelled','retired') — terminal by decision.
Never touched: open/claimed/done/frank_gated rows. The mortician changes NO ticket status;
it only appends a note. Reopening a triaged ticket remains a deliberate human/agent act,
and the marker note is right there telling them why it died.

Env (tests/extension): WQ_DB, MORT_WQ_BIN, MORT_DREAM_DIR, MORT_GRACE_HOURS, MORT_BATCH.
Run periodically (e.g. lumen-run/cron, same cadence family as dispatch_janitor).
"""
import argparse
import datetime
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import self_heal  # noqa: E402 — classify_block/attempt_count, same source janitor trusts

WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
WQ_BIN = os.environ.get("MORT_WQ_BIN", os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq"))
DREAM_DIR = os.environ.get("MORT_DREAM_DIR", "/home/lumen/minerva/dream/morgue")
GRACE_HOURS = float(os.environ.get("MORT_GRACE_HOURS", "48"))
BATCH = int(os.environ.get("MORT_BATCH", "20"))

TERMINAL_STATUSES = ("blocked", "rejected", "cancelled", "retired")

MORGUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS morgue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL UNIQUE,
  status_at_triage TEXT,
  cause TEXT,
  detail TEXT,
  record_path TEXT,
  recorded_at TEXT,
  marked_at TEXT,
  created_at TEXT NOT NULL);
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db():
    c = sqlite3.connect(WQ_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(MORGUE_SCHEMA)
    return c


def is_triaged(tid, con=None):
    """True once the mortician has fully processed this ticket (all three steps done).
    dispatch_janitor calls this before auto-releasing a blocked row: a triaged row is
    do-not-redispatch by definition — the lesson says stop trying, so honor it."""
    own = con is None
    if own:
        try:
            con = db()
        except sqlite3.Error:
            return False  # fail open: an unreadable morgue must not stall the janitor
    try:
        r = con.execute("SELECT marked_at FROM morgue WHERE ticket_id=?", (tid,)).fetchone()
        return bool(r and r["marked_at"])
    except sqlite3.Error:
        return False
    finally:
        if own:
            con.close()


def _parse_ts(raw):
    """wq writes naive-UTC '%Y-%m-%d %H:%M:%S'. None on anything else."""
    try:
        return datetime.datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def classify(row):
    """(cause, detail, skip_reason). cause=None means leave the row alone this pass."""
    status = row["status"]
    if status == "blocked":
        blocked_at = _parse_ts(row["blocked_at"])
        if blocked_at and (datetime.datetime.now(datetime.timezone.utc) - blocked_at
                           ).total_seconds() < GRACE_HOURS * 3600:
            return None, None, "in_grace_window"
        klass, detail = self_heal.classify_block(dict(row))
        if klass == "RUNG_FAULT":
            if self_heal.attempt_count(row["id"], charged_only=True) < self_heal.MAX_HEAL_ATTEMPTS:
                return None, None, "rung_fault_still_healable"  # janitor's patient, not ours
            return "RUNG_FAULT_EXHAUSTED", detail, None
        if klass is None:
            # hand-written / pre-convention reason: still a lesson, still terminal
            return "HAND_BLOCKED", detail, None
        return klass, detail, None
    if status == "rejected":
        return "REVIEW_REJECTED", (row["review_reason"] or row["notes"] or "")[:400], None
    if status == "cancelled":
        return "CANCELLED", (row["notes"] or "")[:400], None
    if status == "retired":
        return "RETIRED", (row["notes"] or "")[:400], None
    return None, None, f"not_terminal:{status}"


def eligible(con):
    return con.execute(
        "SELECT t.* FROM tasks t LEFT JOIN morgue m ON m.ticket_id = t.id "
        "WHERE t.status IN (?,?,?,?) AND (m.ticket_id IS NULL OR m.marked_at IS NULL) "
        "ORDER BY t.id", TERMINAL_STATUSES).fetchall()


def write_record(row, cause, detail):
    """One durable lesson file in the dream proposal sink. Returns its path.
    Overwrite-safe: the filename is keyed by ticket id, so a retry after a crash between
    file write and marker update rewrites the same file instead of littering copies."""
    os.makedirs(DREAM_DIR, exist_ok=True)
    path = os.path.join(DREAM_DIR, f"wq{row['id']}-morgue.md")
    first_note = (row["notes"] or "").strip().splitlines()
    last_note = first_note[-1][:300] if first_note else ""
    body = (
        f"# Morgue: wq#{row['id']} — {cause}\n\n"
        f"PROPOSAL-ONLY (instinct 7): a memory/lesson candidate for Frank review, written by\n"
        f"dispatcher/mortician.py (wq#2486). Nothing auto-edits claude_memory.md from this.\n\n"
        f"- title: {(row['title'] or '')[:200]}\n"
        f"- lane: {row['lane']}  pri: {row['pri']}  status: {row['status']}\n"
        f"- attempts: {self_heal.attempt_count(row['id'])}  "
        f"reopened: {row['reopened_count'] or 0}\n"
        f"- cause: {cause}\n"
        f"- why it died: {(detail or 'no reason recorded')[:600]}\n"
        f"- last note: {last_note}\n"
        f"- verdict: do-not-redispatch. If this cause is wrong, reopen deliberately and say why\n"
        f"  in a note; the mortician never re-triages a marked row.\n"
        f"- triaged: {now()}\n")
    with open(path, "w") as fh:
        fh.write(body)
    return path


def mark_ticket(tid, cause, record_path):
    """Append the do-not-redispatch marker via `wq note` (append-only, wq#1331)."""
    note = (f"[mortician] cause={cause} verdict=do-not-redispatch "
            f"record={record_path} (wq#2486; reopen deliberately, never auto)")
    r = subprocess.run([WQ_BIN, "note", str(tid), note],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, WQ_DB=WQ_DB))
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()[:200]


def run_pass(dry_run=False):
    """Returns (triaged, skipped, errors) counts for this pass."""
    con = db()
    triaged = skipped = errors = 0
    try:
        rows = eligible(con)
        for row in rows[: BATCH * 3]:  # classification skips are cheap; cap real work below
            if triaged >= BATCH:
                break
            tid = row["id"]
            m = con.execute("SELECT * FROM morgue WHERE ticket_id=?", (tid,)).fetchone()
            if m is None:
                cause, detail, skip = classify(row)
                if cause is None:
                    skipped += 1
                    continue
                if dry_run:
                    print(f"would_triage wq#{tid} cause={cause}")
                    triaged += 1
                    continue
                # step 1: freeze the classification, exactly once
                con.execute(
                    "INSERT OR IGNORE INTO morgue(ticket_id, status_at_triage, cause, detail,"
                    " created_at) VALUES (?,?,?,?,?)",
                    (tid, row["status"], cause, (detail or "")[:600], now()))
                con.commit()
                m = con.execute("SELECT * FROM morgue WHERE ticket_id=?", (tid,)).fetchone()
            # step 2: one durable record
            if not m["recorded_at"]:
                if dry_run:
                    print(f"would_record wq#{tid} -> {DREAM_DIR}")
                else:
                    try:
                        path = write_record(row, m["cause"], m["detail"])
                    except OSError as e:
                        print(f"record FAILED wq#{tid}: {e}")
                        errors += 1
                        continue  # marker stays NULL -> retried next pass, no double-fire
                    con.execute("UPDATE morgue SET record_path=?, recorded_at=? "
                                "WHERE ticket_id=? AND recorded_at IS NULL", (path, now(), tid))
                    con.commit()
                    m = con.execute("SELECT * FROM morgue WHERE ticket_id=?", (tid,)).fetchone()
            # step 3: one marker note on the wq row
            if not m["marked_at"]:
                if dry_run:
                    print(f"would_mark wq#{tid}")
                else:
                    ok, out = mark_ticket(tid, m["cause"], m["record_path"] or "")
                    if not ok:
                        print(f"mark FAILED wq#{tid}: {out}")
                        errors += 1
                        continue
                    con.execute("UPDATE morgue SET marked_at=? "
                                "WHERE ticket_id=? AND marked_at IS NULL", (now(), tid))
                    con.commit()
            triaged += 1
            print(f"triaged wq#{tid} cause={m['cause']} record={m['record_path'] or '-'}")
    finally:
        con.close()
    print(f"mortician pass: triaged={triaged} skipped={skipped} errors={errors}")
    return triaged, skipped, errors


DISARM_FILE = os.environ.get("FOREMAN_DISARM_FILE", "/home/lumen/autonomy/DISPATCH_DISARM")


def main():
    ap = argparse.ArgumentParser(description="terminal-failure triage over the wq table (wq#2486)")
    ap.add_argument("--dry-run", action="store_true", help="report, mutate nothing")
    a = ap.parse_args()
    if os.path.exists(DISARM_FILE):
        print(f"disarmed: {DISARM_FILE} present (shared dispatch kill switch), no pass")
        sys.exit(0)
    _, _, errors = run_pass(dry_run=a.dry_run)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
