#!/usr/bin/env python3
"""repair_clobbered_facts.py — wq#2192.

Undo what lumen_verify.py wrote before the probe fix: os_kernel answered every
non-`kernel` predicate with "Linux" (uname -s) and service_state answered every
`status` / `*_status` fact with a `systemctl is-active` word ("inactive").

For each still-open clobber row: close it, and reopen the fact the sweep
superseded (same subject+predicate, valid_to == clobber.valid_from, not itself a
verifier write). With no such predecessor the clobber is only closed, so the
fact reads as unknown rather than wrong. Idempotent: a repaired row is closed
and never selected again.

  repair_clobbered_facts.py [--db PATH] [--apply]     # default is a dry run
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

MINERVA_DB = os.environ.get("MINERVA_DB", "/home/lumen/minerva/minerva.db")

SYSTEMCTL_WORDS = ("active", "inactive", "failed", "activating", "deactivating", "unknown")

CLOBBER_SQL = f"""
SELECT * FROM state_facts
WHERE valid_to IS NULL AND (
  (source_ref = 'verify:os_kernel' AND predicate != 'kernel' AND value = 'Linux')
  OR (source_ref = 'verify:service_state'
      AND value IN ({",".join("?" * len(SYSTEMCTL_WORDS))}))
)
ORDER BY subject, predicate
"""


def repair(conn, apply=False, now=None):
    now = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    plan = []
    for bad in conn.execute(CLOBBER_SQL, SYSTEMCTL_WORDS).fetchall():
        prev = conn.execute(
            "SELECT * FROM state_facts WHERE subject=? AND predicate=? AND valid_to=? "
            "AND COALESCE(source_ref,'') NOT LIKE 'verify:%' "
            "ORDER BY valid_from DESC LIMIT 1",
            (bad["subject"], bad["predicate"], bad["valid_from"]),
        ).fetchone()
        plan.append({
            "subject": bad["subject"], "predicate": bad["predicate"],
            "clobber": bad["value"],
            "restore": prev["value"] if prev else None,
        })
        if not apply:
            continue
        conn.execute("UPDATE state_facts SET valid_to=?, invalidated_at=? WHERE id=?",
                     (now, now, bad["id"]))
        if prev:
            conn.execute("UPDATE state_facts SET valid_to=NULL WHERE id=?", (prev["id"],))
    if apply:
        conn.commit()
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=MINERVA_DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    plan = repair(conn, apply=args.apply)
    for p in plan:
        print(f"{p['subject']}.{p['predicate']}: {p['clobber']!r} -> "
              f"{(p['restore'] or '<closed, no predecessor>')[:60]!r}")
    print(f"{'repaired' if args.apply else 'would repair'} {len(plan)} fact(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
