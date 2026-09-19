#!/usr/bin/env python3
"""backfill_defer_labels.py — wq#2024. Adds `proposals.defer_type_label` (additive,
same convention as wq#1500's `proposals.wq_ticket` column) and labels every existing
`status='defer'` row via defer_type_labels.classify_defer_type(decision_reason).

Additive and idempotent: never touches non-defer rows, safe to re-run, a re-run just
recomputes the label from the current decision_reason. Rows whose reason matches none
of the 7 patterns are left NULL and reported, never guessed onto a label.

Usage (against the live DB, from a host that can reach it):
    python3 backfill_defer_labels.py [--db /home/lumen/minerva/minerva.db] [--dry-run]

This BUILD worker's sandbox cannot reach /home/lumen/minerva/minerva.db (see README.md,
kb/ISSUE-LOG.md) — default path is documented, not exercised, from this session.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from defer_type_labels import classify_defer_type

DEFAULT_DB = "/home/lumen/minerva/minerva.db"


def _ensure_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(proposals)")]
    if "defer_type_label" not in cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN defer_type_label TEXT")


def backfill(db_path, dry_run=False):
    conn = sqlite3.connect(db_path)
    try:
        _ensure_column(conn)
        rows = conn.execute(
            "SELECT id, decision_reason FROM proposals WHERE status = 'defer'"
        ).fetchall()
        labeled, unmatched = 0, []
        for pid, reason in rows:
            label = classify_defer_type(reason)
            if label is None:
                unmatched.append(pid)
                continue
            labeled += 1
            if not dry_run:
                conn.execute(
                    "UPDATE proposals SET defer_type_label = ? WHERE id = ?", (label, pid)
                )
        if not dry_run:
            conn.commit()
        return {"total_defer": len(rows), "labeled": labeled, "unmatched": unmatched}
    finally:
        conn.close()


def _duplicate_suspect_note(db_path):
    """wq#2024 ticket note: duplicate-route false-positive class of bug (wq#2019) lives
    in this exact reason cluster — print the duplicate-suspect rows so a human verifies
    them rather than trusting the label blindly."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM proposals WHERE status = 'defer' AND defer_type_label = 'defer-duplicate-suspect'"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    ids = ", ".join(r[0] for r in rows)
    return (f"defer-duplicate-suspect: {len(rows)} row(s) labeled ({ids}) — VERIFY, "
            "do not trust blindly (wq#2019 false-positive class lives here)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result = backfill(args.db, dry_run=args.dry_run)
    print(f"defer rows: {result['total_defer']}")
    print(f"labeled:    {result['labeled']}{' (dry-run, not written)' if args.dry_run else ''}")
    print(f"unmatched:  {len(result['unmatched'])}")
    if result["unmatched"]:
        print("  " + ", ".join(result["unmatched"]))
        print("  unmatched rows need a human look or a new pattern in defer_type_labels.py "
              "(not guessed onto a label)")

    if not args.dry_run:
        note = _duplicate_suspect_note(args.db)
        if note:
            print(note)


if __name__ == "__main__":
    main()
