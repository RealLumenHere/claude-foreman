#!/usr/bin/env python3
"""wq#1791 pilot 9d95766f5f43 — build the fixed sample ON LUMEN.

Why an extract and not the DB: /home/lumen/minerva is deliberately not a broker read
root (credentials are mirrored into its context sections) and minerva.db is never
copied to a pilot node. This writes only the columns the experiment needs — no
proposal text, no reasoning, no URLs.

Ground truth: `proposals.status`, written by the production decision path, not by
this pilot. Arm labels come from the `classified_intel` row the proposal was
generated from.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'sample.json'
ACCEPTED = ('approved', 'planned', 'staged', 'applied_unverified',
            'deployed', 'shipped', 'piloting')

c = sqlite3.connect('file:/home/lumen/minerva/minerva.db?mode=ro', uri=True, timeout=30)
c.row_factory = sqlite3.Row
rows = c.execute("""
    SELECT p.id, p.created_at, p.status, p.impact,
           i.category AS category, i.source AS source
    FROM proposals p
    JOIN classified_intel i ON i.id = p.source_intel_id
    ORDER BY p.created_at, p.id""").fetchall()

# Buildability evidence: how much of the outcome record can be attributed to a
# prompt-engine bank at all (pe_* research artifacts reaching the proposal path).
pe_intel = c.execute(
    "SELECT COUNT(*) FROM classified_intel WHERE url LIKE '%pe!_%' ESCAPE '!'").fetchone()[0]
pe_props = c.execute(
    "SELECT COUNT(*) FROM proposals p JOIN classified_intel i ON i.id = p.source_intel_id "
    "WHERE i.url LIKE '%pe!_%' ESCAPE '!'").fetchone()[0]
pe_accepted = c.execute(
    "SELECT COUNT(*) FROM proposals p JOIN classified_intel i ON i.id = p.source_intel_id "
    f"WHERE i.url LIKE '%pe!_%' ESCAPE '!' AND p.status IN {ACCEPTED}").fetchone()[0]
intel_total = c.execute('SELECT COUNT(*) FROM classified_intel').fetchone()[0]
c.close()

sample = {
    'accepted_statuses': list(ACCEPTED),
    'attribution': {'intel_total': intel_total, 'intel_from_prompt_engine': pe_intel,
                    'proposals_from_prompt_engine': pe_props,
                    'accepted_from_prompt_engine': pe_accepted},
    'rows': [{'id': r['id'], 'created_at': r['created_at'],
              'category': r['category'], 'source': r['source'],
              'impact': r['impact'],
              'accepted': 1 if r['status'] in ACCEPTED else 0} for r in rows],
}
OUT.write_text(json.dumps(sample, sort_keys=True))
sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
n = len(sample['rows'])
print(f'rows: {n}  accepted: {sum(r["accepted"] for r in sample["rows"])}')
print(f'attribution: {sample["attribution"]}')
print(f'wrote {OUT} ({OUT.stat().st_size} bytes)')
print(f'sha256 {sha}')
