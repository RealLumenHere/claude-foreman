#!/usr/bin/env python3
"""wq#2430 post-install health: live modules import, the provenance contract
exists, and a tag/gate smoke passes. Run: python3 health_check.py [live_dir]"""
import sys, time

LIVE = sys.argv[1] if len(sys.argv) > 1 else '/home/lumen/minerva'
sys.path.insert(0, LIVE)
import minerva_factpack as fp          # noqa: E402
import minerva_verify as mv            # noqa: E402

assert callable(fp.retrieve_evidence) and callable(fp.tag_provenance)
assert isinstance(fp.LAST_EVIDENCE, dict) and set(fp.LAST_EVIDENCE) == {'key', 'chunks'}
assert callable(mv.stale_support_claims) and mv.EVIDENCE_TTL_S >= 0

c, = fp.tag_provenance(['[fact] health check line'], generated_at=time.time())
assert c['source'] == 'fact' and c['stale'] is False and c['ingested_at']
assert fp.tag_provenance(['[capability] m.py: does NOT implement x '
                          '(verified from source, 2020-01-01)'])[0]['stale'] is True
assert mv.stale_support_claims([], []) == ([], None)
print('wq2430 HEALTH OK')
