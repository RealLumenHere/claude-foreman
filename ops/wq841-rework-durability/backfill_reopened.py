#!/usr/bin/env python3
"""wq#841 — one-time, audited seed of tasks.reopened_count from the legacy notes markers.

wq's migration deliberately backfills NOTHING: the legacy signal (`notes LIKE '%REOPENED%'`)
is case-insensitive freeform prose, so it matches genuine markers AND tickets that merely
mention the word. Seeding that straight into a column we are about to call authoritative would
bake the false positives in permanently — the one thing a durable counter must never do.

So the classification is a human's, made once, and recorded on the command line:

    backfill_reopened.py --audit                       # list candidates + marker context
    backfill_reopened.py --seed 433:2026-08-22T04:36:00 --apply

--seed takes ID[:TIMESTAMP] pairs; the timestamp is the reopen time read out of the marker
(falls back to now). Idempotent: a row already at reopened_count > 0 is left alone, so a
re-run after a crash converges instead of double-counting.
"""
import argparse
import datetime
import sqlite3
import sys

DB = "/home/lumen/agents/work-queue/queue.db"
FMT = "%Y-%m-%d %H:%M:%S"
LEGACY_RULE = "notes LIKE '%REOPENED%'"


def candidates(cur):
    return list(cur.execute(
        f"SELECT id, status, reopened_count, notes FROM tasks WHERE {LEGACY_RULE} ORDER BY id"))


def excerpt(notes, width=70):
    k = (notes or "").lower().find("reopened")
    if k < 0:
        return "(no literal match — LIKE is case-insensitive, check for unicode/case variants)"
    return (notes[max(0, k - width):k + width]).replace("\n", " ")


def cmd_audit(args):
    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = candidates(c)
    print(f"{len(rows)} row(s) match the legacy rule ({LEGACY_RULE}) in {args.db}\n")
    for tid, status, count, notes in rows:
        print(f"#{tid} status={status} reopened_count={count}")
        print(f"    ...{excerpt(notes)}...\n")
    print("Classify each by hand: a genuine marker is a reopen; a ticket that merely mentions")
    print("the word (a report quoting the metric, a note about SOME OTHER ticket) is not.")
    c.close()
    return 0


def parse_seed(spec):
    seeds = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        tid, _, ts = item.partition(":")
        try:
            tid = int(tid)
        except ValueError:
            sys.exit(f"REFUSED: --seed item {item!r} is not ID or ID:TIMESTAMP")
        if ts:
            ts = ts.replace("T", " ")
            try:
                datetime.datetime.strptime(ts, FMT)
            except ValueError:
                sys.exit(f"REFUSED: --seed item {item!r} has a timestamp that is not {FMT}")
        seeds[tid] = ts or None
    if not seeds:
        sys.exit("REFUSED: --seed listed no ticket ids")
    return seeds


def cmd_seed(args):
    seeds = parse_seed(args.seed)
    now = datetime.datetime.now(datetime.timezone.utc).strftime(FMT)
    c = sqlite3.connect(args.db)
    c.execute("BEGIN IMMEDIATE")
    applied, skipped, missing = [], [], []
    for tid, ts in sorted(seeds.items()):
        row = c.execute("SELECT status, reopened_count FROM tasks WHERE id=?", (tid,)).fetchone()
        if row is None:
            missing.append(tid)
            continue
        if (row[1] or 0) > 0:
            skipped.append((tid, row[1]))
            continue
        if args.apply:
            c.execute("UPDATE tasks SET reopened_count=1, reopened_at=? WHERE id=?", (ts or now, tid))
        applied.append((tid, row[0], ts or now))
    c.execute("COMMIT" if args.apply else "ROLLBACK")
    c.close()

    verb = "seeded" if args.apply else "WOULD seed (dry-run — pass --apply)"
    for tid, status, ts in applied:
        print(f"{verb} #{tid} status={status} reopened_count=1 reopened_at={ts}")
    for tid, count in skipped:
        print(f"skipped #{tid} — already at reopened_count={count} (idempotent, not re-counted)")
    for tid in missing:
        print(f"MISSING #{tid} — no such ticket")
    print(f"{len(applied)} {'applied' if args.apply else 'pending'}, "
          f"{len(skipped)} skipped, {len(missing)} missing")
    return 1 if missing else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DB)
    p.add_argument("--audit", action="store_true", help="list legacy-rule candidates and exit")
    p.add_argument("--seed", help="ID[:TIMESTAMP][,ID[:TIMESTAMP]...] to seed at reopened_count=1")
    p.add_argument("--apply", action="store_true", help="write; without it, dry-run")
    a = p.parse_args()
    if a.audit:
        return cmd_audit(a)
    if a.seed:
        return cmd_seed(a)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
