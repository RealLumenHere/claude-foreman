#!/usr/bin/env python3
"""Pilot 7c516dfb1a14 runner. See CARD.md for hypothesis/metric/win condition.

baseline  -- today's split-schema shape: one normalized table + indexed tag
             junction table per kind (archival_memory, research_doc), merged
             in Python for a cross-kind tag lookup.
treatment -- the proposal's shape: one agent-centric table, tags as a JSON
             array column, one SQL query, no app-side merge.
"""
import argparse
import json
import random
import sqlite3
import statistics
import time

from build_sample import TAGS

QUERY_SEED = 424242


def load_records(path):
    with open(path) as f:
        return json.load(f)["records"]


def setup_baseline(conn, records):
    conn.execute("CREATE TABLE archival_memory(id TEXT PRIMARY KEY, created_at INT, content TEXT, source TEXT)")
    conn.execute("CREATE TABLE archival_memory_tags(record_id TEXT, tag TEXT)")
    conn.execute("CREATE INDEX idx_am_tag ON archival_memory_tags(tag)")
    conn.execute("CREATE TABLE research_doc(id TEXT PRIMARY KEY, created_at INT, content TEXT, source TEXT)")
    conn.execute("CREATE TABLE research_doc_tags(record_id TEXT, tag TEXT)")
    conn.execute("CREATE INDEX idx_rd_tag ON research_doc_tags(tag)")
    for r in records:
        table = "archival_memory" if r["kind"] == "archival_memory" else "research_doc"
        conn.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", (r["id"], r["created_at"], r["content"], r["source"]))
        conn.executemany(f"INSERT INTO {table}_tags VALUES (?,?)", [(r["id"], t) for t in r["tags"]])
    conn.commit()


def query_baseline(conn, tag, limit=20):
    rows = conn.execute(
        "SELECT am.id, am.created_at FROM archival_memory am "
        "JOIN archival_memory_tags t ON t.record_id = am.id WHERE t.tag = ?",
        (tag,),
    ).fetchall()
    rows += conn.execute(
        "SELECT rd.id, rd.created_at FROM research_doc rd "
        "JOIN research_doc_tags t ON t.record_id = rd.id WHERE t.tag = ?",
        (tag,),
    ).fetchall()
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


def setup_treatment(conn, records):
    conn.execute(
        "CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT, created_at INT, "
        "content TEXT, source TEXT, tags TEXT)"
    )
    for r in records:
        conn.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            (r["id"], r["kind"], r["created_at"], r["content"], r["source"], json.dumps(r["tags"])),
        )
    conn.commit()


def query_treatment(conn, tag, limit=20):
    return conn.execute(
        "SELECT id, created_at FROM records "
        "WHERE EXISTS (SELECT 1 FROM json_each(records.tags) je WHERE je.value = ?) "
        "ORDER BY created_at DESC LIMIT ?",
        (tag, limit),
    ).fetchall()


def run(arm, sample_path, n_queries):
    records = load_records(sample_path)
    conn = sqlite3.connect(":memory:")
    t0 = time.perf_counter()
    if arm == "baseline":
        setup_baseline(conn, records)
        query_fn = query_baseline
    else:
        setup_treatment(conn, records)
        query_fn = query_treatment
    build_ms = (time.perf_counter() - t0) * 1000

    rng = random.Random(QUERY_SEED)
    tag_seq = [rng.choice(TAGS) for _ in range(n_queries)]
    latencies_ms = []
    for tag in tag_seq:
        t0 = time.perf_counter()
        query_fn(conn, tag)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

    latencies_ms.sort()
    return {
        "arm": arm,
        "n_records": len(records),
        "n_queries": n_queries,
        "build_ms": round(build_ms, 3),
        "p50_ms": round(statistics.median(latencies_ms), 4),
        "p95_ms": round(statistics.quantiles(latencies_ms, n=20)[18], 4),
        "mean_ms": round(statistics.mean(latencies_ms), 4),
        "min_ms": round(min(latencies_ms), 4),
        "max_ms": round(max(latencies_ms), 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result = run(args.arm, args.sample, args.queries)

    try:
        with open(args.out) as f:
            all_results = json.load(f)
    except FileNotFoundError:
        all_results = {}
    all_results[args.arm] = result
    with open(args.out, "w") as f:
        json.dump(all_results, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
