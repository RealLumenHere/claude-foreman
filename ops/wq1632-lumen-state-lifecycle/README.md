# wq#1632 — lifecycle predicate, sibling closure, predicate registry gate

Gated write path for minerva `state_facts`. See `DESIGN.md` for the full design
(MINERVA_PIPELINE_MAP.md §3.2/§4.3/§5#4).

- `state_lifecycle.py` — module + CLI (`ingest`, `backfill [--apply]`,
  `close-on-up`, `seed-registry`). Deployed as `lumen-state-lifecycle`.
- `registry.seed.yaml` — predicate registry seed; deployed to
  `/home/lumen/minerva/verify/registry.yaml`, shared with wq#1613.
- `test_state_lifecycle.py` — `python3 -m pytest` here; fixture DB mirrors the
  live thoth/nephthys drift evidence.
- `install.sh` — deploy + dry-run migration; `--apply` is a deliberate second step.

Golden-set recall bench: unaffected by construction — no recall-path file changes;
`state_facts` feeds recall only as subject vocabulary and closure removes no subject.
