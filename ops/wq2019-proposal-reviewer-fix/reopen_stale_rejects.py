#!/usr/bin/env python3
"""wq#2019 — requeue proposals killed by the two reviewer bugs for genuine re-review.

minerva_proposal.py marks a classified_intel row processed=1 the moment it produces
ANY outcome for it (reject, duplicate, accept, ...) — see mark_processed(). To get a
real re-review out of the now-fixed dedup gate and hardware invariants, the source
classified_intel row must go back to processed=0 so the next minerva-proposal.service
run (get_candidates(), LIMIT 10 per run, ordered by self_relevance DESC) picks it back
up and regenerates a fresh proposal through the fixed code. The original rejected
`proposals` row is left untouched as an audit trail; re-processing creates a new row.

Two categories, matched by decision_reason (see README.md for how these were derived
and counted against the live DB before this script existed):
  route   - 'Duplicate: LiteLLM route "..." already exists in litellm_config.yaml'
  hardware - status='reject' and reason mentions Broadwell/AVX-512/no GPU/CPU-only

Fail-safe: dry-run by default, prints the count and a sample; --apply required to
write. Idempotent: only touches classified_intel rows currently processed=1.
"""
import argparse
import sqlite3

DB = '/home/lumen/minerva/minerva.db'

ROUTE_WHERE = "p.decision_reason LIKE 'Duplicate: LiteLLM route%already exists%'"
HARDWARE_WHERE = (
    "p.status='reject' AND ("
    "p.decision_reason LIKE '%Broadwell%' OR "
    "p.decision_reason LIKE '%AVX-512%' OR "
    "p.decision_reason LIKE '%no GPU%' OR "
    "p.decision_reason LIKE '%CPU-only%')"
)

SELECT_SQL = f"""
    SELECT DISTINCT p.source_intel_id, c.title
    FROM proposals p
    JOIN classified_intel c ON p.source_intel_id = c.id
    WHERE c.processed = 1 AND (({ROUTE_WHERE}) OR ({HARDWARE_WHERE}))
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='write the change; default is dry-run')
    ap.add_argument('--db', default=DB)
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(SELECT_SQL).fetchall()
    print(f'{len(rows)} classified_intel rows match a wq#2019 false-positive reject reason')
    for r in rows[:10]:
        print(f'  {r["source_intel_id"]}  {(r["title"] or "")[:70]}')
    if len(rows) > 10:
        print(f'  ... and {len(rows) - 10} more')

    if not a.apply:
        print('dry-run: nothing written (pass --apply to requeue)')
        conn.close()
        return 0

    ids = [r['source_intel_id'] for r in rows]
    conn.executemany('UPDATE classified_intel SET processed = 0 WHERE id = ?',
                      [(i,) for i in ids])
    conn.commit()
    n = conn.execute(
        'SELECT count(*) FROM classified_intel WHERE processed = 0 AND id IN '
        f'({",".join("?" * len(ids))})', ids).fetchone()[0] if ids else 0
    conn.close()
    print(f'requeued {n}/{len(ids)} rows (processed=0) for re-review by the fixed reviewer')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
