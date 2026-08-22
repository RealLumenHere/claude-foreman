#!/usr/bin/env python3
"""wq#811 — count archetypes in the work queue."""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestrator.router import classify_archetype

DB_PATH = "/home/lumen/agents/work-queue/queue.db"


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cursor = conn.cursor()

    cursor.execute("SELECT title, detail, lane FROM tasks")
    rows = cursor.fetchall()
    conn.close()

    archetype_counts = {}
    for title, detail, lane in rows:
        archetype, _ = classify_archetype(title, detail or "", lane or "")
        archetype_counts[archetype] = archetype_counts.get(archetype, 0) + 1

    sorted_archetypes = sorted(archetype_counts.items(), key=lambda x: -x[1])
    for archetype, count in sorted_archetypes:
        print(f"{archetype:30} {count:6}")


if __name__ == "__main__":
    main()
