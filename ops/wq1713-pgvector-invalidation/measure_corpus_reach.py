#!/usr/bin/env python3
"""wq#1713 — read-only reach measurement for the pgvector corpus.

Answers the question research/PGVECTOR_INVALIDATION_CONTRACTS.md §6 fix #1 asks:
how many of the 10,566 research docs assert a fleet value that `state_facts` has
since superseded? Mirrors ops/wq1609-minerva-contradiction-scan/measure_reach.py
for the corpus instead of for the closed-fact index.

Reuses wq#1609's matching wholesale — no new matching logic, so a hit here means
the same thing a hit in a note or FLEET_MAP.md means. Writes nothing anywhere.

    lumen-run start --name reach -- python3 measure_corpus_reach.py
"""
import os, pathlib, sys, time

sys.path.insert(0, "/home/lumen/foreman/ops/wq1609-minerva-contradiction-scan")
import lumen_contradiction_scan as cs  # noqa: E402

ENV = {}
for line in pathlib.Path("/home/lumen/agent/.env").read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        ENV[k.strip()] = v.strip().strip('"').strip("'")


def pg():
    import psycopg2

    conn = psycopg2.connect(
        host=ENV["LUMEN_PGVECTOR_HOST"], port=int(ENV["LUMEN_PGVECTOR_PORT"]),
        dbname=ENV["LUMEN_PGVECTOR_DB"], user=ENV["LUMEN_PGVECTOR_USER"],
        password=ENV["LUMEN_PGVECTOR_PASSWORD"],
    )
    cur = conn.cursor()
    cur.execute("SET default_transaction_read_only = on")
    conn.commit()
    return conn


def candidates(cur, entry):
    """The §4.2 prefilter: coarse containment in Postgres, exact matching in
    Python. A self-identifying value carries its own subject, so containment is
    enough to be a candidate; a bare label needs its subject present too."""
    like = f"%{entry['dead_value']}%"
    if entry["scoped"]:
        cur.execute(
            "SELECT id, filepath, content FROM research_docs "
            "WHERE content ILIKE %s AND content ILIKE %s",
            (like, f"%{entry['subject']}%"),
        )
    else:
        cur.execute(
            "SELECT id, filepath, content FROM research_docs WHERE content ILIKE %s",
            (like,),
        )
    return cur.fetchall()


def main():
    db = os.environ.get("MINERVA_DB", cs.MINERVA_DB)
    index = cs.build_dead_value_index(cs.connect(db, readonly=True))
    print(f"dead-value index entries: {len(index)}")

    conn = pg()
    cur = conn.cursor()
    t0 = time.time()
    scanned, hits = set(), []
    for entry in index:
        for doc_id, filepath, content in candidates(cur, entry):
            scanned.add(doc_id)
            lines = content.splitlines(keepends=True)
            for n, line in enumerate(lines, 1):
                hits += cs.scan_text(
                    line, [entry], f"research_docs:{doc_id}:{n}", "doc_row",
                    subject_context=cs.enclosing_context(lines, n - 1),
                    changelog_context=content,
                )
    conn.close()

    action = [h for h in hits if h["severity"] == "action"]
    print(f"prefilter candidates (distinct docs): {len(scanned)}")
    print(f"elapsed: {time.time() - t0:.1f}s")
    print(f"hits: {len(hits)} total, {len(action)} action, {len(hits) - len(action)} info")
    for h in action[:20]:
        print(f"  {h['location']}  {h['subject']}.{h['predicate']} = "
              f"{h['dead_value']!r} (now {h['current_value']!r})")
        print(f"      {h['excerpt'][:140]}")


if __name__ == "__main__":
    main()
