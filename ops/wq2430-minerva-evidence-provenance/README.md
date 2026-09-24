# wq#2430 — provenance-tagged evidence + stale-TTL gate in the verify path

Proposal 2b10d09072d1 (factory lane RED, hand-applied under lumen-change-safety).

## Problem

Retrieved evidence chunks in the Minerva verify path were bare strings: no
record of where a chunk came from or when it was probed, so `minerva_verify`
could accept a claim whose only support was months-old evidence (in practice:
`[capability]` card lines, whose "verified from source, <date>" stamps age
while every other probe is re-run each corpus build).

## Change

- `minerva_factpack.py`
  - Corpus cache v2: `{'generated_at': epoch, 'lines': [...]}` (v1 bare-list
    caches still read; ingest time falls back to cache mtime). No DB migration.
  - `tag_provenance(lines)` — the new retrieval contract: each chunk becomes
    `{'text', 'source', 'source_ref' (origin URI), 'ingested_at' (UTC ISO),
    'stale'}`. `stale` = ingested more than `FACTPACK_EVIDENCE_TTL` seconds ago
    (default 7 days, 0 disables). `[capability]` lines take the card's own
    generation date; everything else takes the corpus build time.
  - `retrieve_evidence()` = `retrieve()` + provenance.
  - `build_context()` records the provenance of exactly the fact lines it
    included in `LAST_EVIDENCE`, keyed by sha1(answer text).
- `minerva_verify.py`
  - `stale_support_claims(claims, evidence)` — flags claims whose ONLY
    semantic support (cosine >= `COSINE_GROUNDED`; keyword overlap when the
    embedder is down) among the evidence chunks is stale per
    `VERIFY_EVIDENCE_TTL_S` (default 7 days, 0 = kill switch). Claims with no
    support at all stay the citation/judge layers' job. Fails open on error.
  - `verify(..., evidence=None)` — new optional kwarg; when absent it picks up
    the factpack `LAST_EVIDENCE` handoff for exactly this answer (hash-keyed),
    so the live proposal path gets the gate with NO caller change. Flagged
    claims force `passed=False` with reason `stale_evidence(n)`; the latent
    rescue layer cannot flip that back (it can't see provenance). Result gains
    `stale_support_claims` + `evidence_chunks`.
  - The gate only ever tightens; with no evidence it is a no-op (pre-change
    behavior, matching the proposal's rollback contract).

Explicitly NOT built (per ticket): no knowledge graph, no new orchestration
layer, no new service, no schema migration.

## Install / verify / rollback

```
/home/lumen/bin/lumen-run start --root --name wq2430-provenance -- \
    /home/lumen/foreman/ops/wq2430-minerva-evidence-provenance/install.sh
```

install.sh is gated: pre-image pin (line counts 332/611 + anchor lines +
live==mirror `cmp`), staged-copy precheck (py_compile, bounded diff — a staged
copy replacing >15 pre-existing lines aborts, offline 22-test suite), backup to
`/home/lumen/backups/wq2430/` with a sha256 manifest, then apply + health
(`health_check.py`: live import, contract symbols, tag/gate smoke).

Rollback: `install.sh rollback` (restores the two `.bak` files); or env kill
switch `VERIFY_EVIDENCE_TTL_S=0` without touching code.

Tests: `python3 test_wq2430_provenance.py` — fully offline (dotenv/requests
stubbed, embedder faked, DB/log redirected), 22 tests.

Known pre-existing issue surfaced by health (NOT this change): factpack CAP
ordering log fires — compact digest grew to ~18.1K so digest+closed-world+
reserve = 22835 > CAP 22500 (wq#1383 class, 4th occurrence). Facts still fit
the 23000 judge window; needs a FACTPACK_CAP bump in a follow-up.
