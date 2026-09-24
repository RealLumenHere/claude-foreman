#!/usr/bin/env python3
"""wq#2460 post-apply health: import the LIVE minerva_verify, prove the
context-authority surface exists, the live state_facts schema satisfies the
new SELECT, and the classifier behaves — without one judge or recall call.

Usage: python3 health_check.py /home/lumen/minerva
"""
import inspect, sys

LIVE = sys.argv[1] if len(sys.argv) > 1 else '/home/lumen/minerva'
sys.path.insert(0, LIVE)

import minerva_verify as mv

# 1. surface
for sym in ('classify_premise_authority', 'context_authority_labels',
            '_authority_block', '_state_fact_rows', 'AUTHORITY_ENABLED',
            'AUTHORITY_CAP', 'AUTHORITY_TTL_S'):
    assert hasattr(mv, sym), f'missing symbol: {sym}'

# 2. signatures still back-compat
sig = inspect.signature(mv.verify)
assert list(sig.parameters) == ['answer', 'context', 'subject', 'model', 'mode',
                                'evidence'], f'verify signature drifted: {sig}'
assert 'authority_note' in inspect.signature(mv._judge_once).parameters
assert 'authority_note' in inspect.signature(mv.judge_entailment_uq).parameters

# 3. live schema satisfies the new read-only SELECT
rows = mv._state_fact_rows()
assert isinstance(rows, list) and (not rows or len(rows[0]) == 5), \
    f'state_facts SELECT shape unexpected: {rows[:1]}'
print(f'state_facts readable: {len(rows)} rows')

# 4. classifier semantics on synthetic rows (recall stubbed — no live calls)
no_hits = lambda p: []
open_fact = ('gateway', 'runs', 'litellm gateway active port 4000', '', None)
closed = ('gateway', 'runs', 'litellm gateway active port 4000', '', '2026-01-01')
assert mv.classify_premise_authority(
    'The litellm gateway is active on port 4000.', [open_fact], recall=no_hits) == 'AUTHORITATIVE'
assert mv.classify_premise_authority(
    'The litellm gateway is active on port 4000.', [closed], recall=no_hits) == 'STALE'
assert mv.classify_premise_authority(
    'Entirely unknown premise about quantum widgets.', [open_fact], recall=no_hits) == 'UNVERIFIED'

# 5. prompt block
assert mv._authority_block(['AUTHORITATIVE']) == ''
blk = mv._authority_block(['STALE', 'UNVERIFIED'])
assert 'claim 1: STALE' in blk and 'claim 2: UNVERIFIED' in blk

print('wq2460 HEALTH OK')
