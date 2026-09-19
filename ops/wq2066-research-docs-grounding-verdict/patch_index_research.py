#!/usr/bin/env python3
"""patch_index_research.py — wq#2066: make index_research.py write grounding_verdict.

/home/lumen/minerva/** is outside the build lane's write roots (ISSUE-LOG #272), so
the indexer edit ships as an anchored, idempotent patcher for a session with
minerva reach rather than as a hand-typed diff. Every anchor is asserted to occur
exactly once; a drifted file aborts untouched.

  python3 patch_index_research.py /home/lumen/minerva/retrieval/index_research.py
  python3 patch_index_research.py FILE --check     # exit 0 if already patched

grounding_verdict.py must sit next to index_research.py (it is imported from the
script's own directory, the same way asserted_at is).
"""
import sys

MARK = "wq#2066"

EDITS = [
    ("from asserted_at import asserted_at as doc_asserted_at   # wq#1773\n",
     "from asserted_at import asserted_at as doc_asserted_at   # wq#1773\n"
     "from grounding_verdict import parse_header as doc_grounding   # wq#2066\n"),
    ("INSERT INTO research_docs (filepath, filename, category, mtime, size_bytes, content, content_hash, embedding, asserted_at)",
     "INSERT INTO research_docs (filepath, filename, category, mtime, size_bytes, content, content_hash, embedding, asserted_at,\n"
     "                               grounding_verdict, asserts_fleet_state)"),
    ("embedding=EXCLUDED.embedding, indexed_at=now(), asserted_at=EXCLUDED.asserted_at;",
     "embedding=EXCLUDED.embedding, indexed_at=now(), asserted_at=EXCLUDED.asserted_at,\n"
     "      grounding_verdict=EXCLUDED.grounding_verdict, asserts_fleet_state=EXCLUDED.asserts_fleet_state;"),
    ('template="(%s,%s,%s,%s,%s,%s,%s,%s,%s)")',
     'template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")'),
    ("                          doc_asserted_at(content, mtime)))\n",
     "                          doc_asserted_at(content, mtime),\n"
     "                          # wq#2066: the `grounding:` header fleet_grounding wrote; None (kept\n"
     "                          # in RAG) for a doc without one, which backfill assesses later.\n"
     "                          *doc_grounding(content)))\n"),
]


def patch(src):
    if MARK in src:
        return src, False
    for old, _ in EDITS:
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"anchor found {n}x (want 1), file untouched: {old[:70]!r}")
    for old, new in EDITS:
        src = src.replace(old, new)
    return src, True


def main(argv):
    path = argv[1]
    src = open(path).read()
    out, changed = patch(src)
    if "--check" in argv:
        return 0 if not changed else 1
    if changed:
        open(path + ".bak.wq2066", "w").write(src)
        open(path, "w").write(out)
    print("patched" if changed else "already patched")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
