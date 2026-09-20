#!/usr/bin/env python3
"""wq#1713 — read-only reach measurement for the pgvector corpus.

Answers the question research/PGVECTOR_INVALIDATION_CONTRACTS.md §6 fix #1 asks:
how many research docs assert a fleet value that `state_facts` has since
superseded? Mirrors ops/wq1609-minerva-contradiction-scan/measure_reach.py for
the corpus instead of for the closed-fact index.

Since wq#1775 this calls the *shipped* corpus tier (`cs.scan_corpus`), not a
copy of it, so the number it prints is the number the timer would print. The
re-scope gate lives here: the `--corpus-apply` writer stays off until this
script reports true positives above zero (§4.2 measured 0/5 on 2026-09-11,
before wq#1781 narrowed self_identifying() and before the two prose narrowings).

Writes nothing anywhere.

    lumen-run start --name reach -- python3 measure_corpus_reach.py
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
# The sibling in this checkout first: a measurement that imports the *installed*
# scanner cannot measure the branch under review.
for candidate in (os.path.join(HERE, os.pardir, "wq1609-minerva-contradiction-scan"),
                  "/home/lumen/foreman/ops/wq1609-minerva-contradiction-scan"):
    if os.path.exists(os.path.join(candidate, "lumen_contradiction_scan.py")):
        sys.path.insert(0, os.path.abspath(candidate))
        break
import lumen_contradiction_scan as cs  # noqa: E402


def main():
    db = os.environ.get("MINERVA_DB", cs.MINERVA_DB)
    index = cs.build_dead_value_index(cs.connect(db, readonly=True))
    print(f"scanner: {os.path.dirname(os.path.abspath(cs.__file__))}")
    print(f"dead-value index entries: {len(index)}")

    conn = cs.corpus_connect(readonly=True)
    t0 = time.time()
    try:
        hits, docs = cs.scan_corpus(conn.cursor(), index)
    finally:
        conn.close()

    action = [h for h in hits if h["severity"] == "action"]
    chunks = {(h["doc_id"], h["chunk_ix"]) for h in hits}
    print(f"prefilter candidates (distinct docs): {docs}")
    print(f"elapsed: {time.time() - t0:.1f}s")
    print(f"hits: {len(hits)} total, {len(action)} action, "
          f"{len(hits) - len(action)} info, across {len(chunks)} chunk(s)")
    for h in action[:20]:
        print(f"  {h['location']} chunk {h['chunk_ix']} @{h['char_start']} "
              f"[{h['doc_kind']}]  {h['subject']}.{h['predicate']} = "
              f"{h['dead_value']!r} (now {h['current_value']!r})")
        print(f"      {os.path.basename(h['filepath'])}: {h['excerpt'][:140]}")


if __name__ == "__main__":
    main()
