#!/usr/bin/env python3
"""wq#1559 pilot 0eed2f5bdb92 — build the fixed sample ON LUMEN.

Why here and not on hathor: the query vectors need nomic-embed-text, and the model
lives on Lumen's ollama. Embedding is the ONLY step that runs here; both arms of the
experiment run on hathor over vectors and text alone, with no model, so neither arm
gets an advantage from where it executed.

Why an extract and not the DB: /home/lumen/minerva is deliberately not a broker read
root (credentials are mirrored into its context sections) and minerva.db is never
copied to a pilot node. This writes only the columns the experiment needs.

Ground truth: the 50 proposals the live dedup gate suppressed as duplicates, each
carrying the parent_id it was suppressed against. That label was written by the
production path at suppression time, not by this pilot.
"""
import json, sqlite3, struct, sys
from pathlib import Path

sys.path.insert(0, '/home/lumen/minerva')
import minerva_dedup as md

OUT = Path('/home/lumen/foreman/pilots/0eed2f5bdb92/sample.json')
c = sqlite3.connect('/home/lumen/minerva/minerva.db', timeout=30)
c.row_factory = sqlite3.Row

index = [{'proposal_id': r['proposal_id'], 'title': r['title'],
          'vec': list(struct.unpack(f'{len(r["vec"])//4}f', r['vec']))}
         for r in c.execute('SELECT proposal_id, title, vec FROM proposal_dedup '
                            'WHERE vec IS NOT NULL')]
print(f'index rows: {len(index)}')

dups = c.execute("SELECT id, parent_id, title, proposed_change FROM proposals "
                 "WHERE status='duplicate' AND parent_id IS NOT NULL AND parent_id<>'' "
                 "ORDER BY id").fetchall()
c.close()

known = {i['proposal_id'] for i in index}
queries, skipped = [], 0
for r in dups:
    if r['parent_id'] not in known:
        skipped += 1
        continue
    # Same text shape the live gate indexes with (minerva_proposal.py record call).
    text = f"{r['title'] or ''}\n{(r['proposed_change'] or '')[:600]}"
    v = md.embed(text)
    if not v:
        skipped += 1
        continue
    queries.append({'id': r['id'], 'parent_id': r['parent_id'],
                    'title': r['title'], 'text': text, 'vec': v})
    print(f'  embedded {r["id"]} -> parent {r["parent_id"]}')

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    'queries': queries, 'index': index,
    'threshold': md.THRESHOLD, 'entity_share': md.ENTITY_SHARE,
    'embed_model': md.EMBED_MODEL,
}))
print(f'queries: {len(queries)} skipped: {skipped}')
print(f'wrote {OUT} ({OUT.stat().st_size} bytes)')
