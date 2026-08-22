#!/usr/bin/env python3
# usage: python3 research/ticket_archetype_census.py
"""Read-only census of ticket archetypes across queue.db's tasks table."""

import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.router import classify_archetype

DB_PATH = "/home/lumen/agents/work-queue/queue.db"


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute("SELECT title, detail, lane FROM tasks").fetchall()
    conn.close()

    counts = Counter()
    for title, detail, lane in rows:
        archetype, _ = classify_archetype(title, detail, lane)
        counts[archetype] += 1

    for archetype, count in counts.most_common():
        print(f"{archetype}\t{count}")


if __name__ == "__main__":
    main()
