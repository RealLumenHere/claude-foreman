#!/usr/bin/env python3
"""Rebuild the golden fixture for test_capability_replay.py's live-four drill.

    build_four_tickets_fixture.py [DB]

wq#2921: test_live_replay_of_the_four_stuck_tickets read the LIVE queue.db directly, so
the drill silently depended on wq#1401/1402/1403/1407's live ticket text — when 1402's
detail was rewritten (Minerva upgrade-batch feed, 2026-09-03) it stopped citing a path
outside allow.read_dirs, and the drill's `advisory` assertion broke on drift it was never
meant to track. This snapshots the four rows verbatim (the fields capability_replay.
load_tickets reads) so the drill replays a pinned fixture, not the mutable live db.
Re-run this script and re-diff by hand if the four tickets' real text ever needs a
deliberate refresh; the drill test itself should not silently follow live edits again.
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
TICKET_IDS = (1401, 1402, 1403, 1407)
FIELDS = "id, lane, status, title, detail, needs, archetype"


def build(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        qmarks = ",".join("?" for _ in TICKET_IDS)
        rows = con.execute(f"select {FIELDS} from tasks where id in ({qmarks}) order by id",
                           TICKET_IDS).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def main(argv):
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB
    rows = build(db_path)
    got = {r["id"] for r in rows}
    missing = set(TICKET_IDS) - got
    if missing:
        print(f"missing ticket ids in {db_path}: {sorted(missing)}", file=sys.stderr)
        return 1
    dest = os.path.join(HERE, "four_stuck_tickets.json")
    with open(dest, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"{len(rows)} rows -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
