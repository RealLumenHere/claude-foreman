# wq#1632 — lifecycle predicate, sibling closure, predicate registry gate

Source: research/MINERVA_PIPELINE_MAP.md §3.2 (live thoth/nephthys drift), §4.3 (design),
§5#4 (migration slot). Companion tickets: wq#1613 (verifier registry — shares the registry
file), wq#1608 (reconfirm-on-match reconciler — consumes the `close-on-up` hook).

## Problem (live evidence, 2026-09-10)

- `thoth.status = tailnet-OFFLINE` (valid_from 09-02) and `thoth.tailnet_routing = FIXED`
  (valid_from 09-08) are both open. Supersession keys on `(subject, predicate)`, so a fix
  recorded under a *new* predicate cannot close the stale sibling.
- `nephthys.lifecycle = decommissioned` is open, yet `serving_model`, `embed_model`,
  `litellm_aliases`, `dc_handle_store`, `ollama_version` are still open rows. Nothing links
  a lifecycle change to the operational facts it invalidates.
- Predicate vocabulary is ungoverned: any writer can invent a new axis
  (`tailnet_routing`) instead of superseding an existing one (`status`).

## Design

### 1. Reserved predicate `lifecycle`

Closed enum: `provisioning | active | degraded | offline | decommissioned | retired`.
`status` stays free-text for the story; `lifecycle` is what tools branch on. Ingest of
`lifecycle` with any other value is refused.

Backfill is conservative, from the current open `status`/`role` prose, first match wins:

| prose contains | lifecycle |
|---|---|
| decommission* | decommissioned |
| retired | retired |
| offline / unreachable / dead / no remote shell | offline |
| degraded / flaky | degraded |
| provisioning / bring-up / bringup | provisioning |
| anything else | active + a coverage line in the report |

The backfilled fact inherits `valid_from` from the source fact (the claim is only as old
as its evidence), `confidence 0.6`, `source_ref = lifecycle-backfill:wq1632 fact:<id>`.
Subjects that already have an open `lifecycle` row (nephthys) are never overwritten.

### 2. Sibling closure on decommission

`ingest(subject, 'lifecycle', 'decommissioned'|'retired')` closes every open fact on that
subject whose predicate carries `closes_on_decommission: true` in the registry:
`valid_to = <lifecycle valid_from>`, `invalidated_at = now`,
`source_ref = lifecycle-closure:<lifecycle fact id>` (ticket-verbatim; the superseding
lifecycle row is the provenance trail). Identity/hardware predicates stay open — a
decommissioned Mac still has 64 GB. The operational list is data, not code: per-predicate
flags plus glob patterns (`*_status`, `mcp_*`) in the shared registry.

The migration applies the same closure to subjects whose lifecycle is *already*
decommissioned/retired (nephthys). Dry-run is the default; `--apply` writes.

### 3. Predicate registry gate

`ingest()` refuses a predicate not present in the registry (exact name or pattern match)
unless `--new-predicate "<why>"` is passed. The refusal names the nearest existing
predicates (difflib) — this is what would have turned `thoth.tailnet_routing=FIXED` into a
supersession of `thoth.status`, or at least made the writer say out loud that it is a new
axis. `--new-predicate` appends the predicate to the registry file with `added_by`, `why`,
and date, so vocabulary growth is deliberate and audited in the file's git history.

### 4. Status-class facts close on observed-up (wq#1608 hook)

A `status`/`lifecycle=offline` fact is a claim about an interval. `close-on-up <subject>`
closes every open registry-class-`status` fact on the subject at the observed time and
supersedes `lifecycle → active`. The wq#1608 reconciler calls this when it observes the
node up; for thoth this closes both `status=OFFLINE` and `tailnet_routing=FIXED` in one
observation instead of leaving them to rot. This module does not probe anything itself —
fail-open stays with the reconciler.

## Registry file (shared with wq#1613)

Deployed target: `/home/lumen/minerva/verify/registry.yaml` (the path wq#1613 already
plans as the verifier registry; MINERVA_PIPELINE_MAP.md:351 puts `closes_on_decommission`
there explicitly). Schema here defines `class`, `closes_on_decommission`, `reserved`,
`enum`, `patterns`; wq#1613 adds `probe`, `verify_interval_days`, `source_kind` per
predicate — loaders on both sides must tolerate unknown keys. `seed-registry` merges
`SELECT DISTINCT predicate` from the live DB into the file at deploy time so the shipped
seed never has to guess the full live vocabulary (96 subjects, ~1152 rows).

## What this deliberately does not do

- No edit to the live `/home/lumen/minerva/lumen_state.py` from this lane (outside the
  worker sandbox, and blind-patching a file we cannot read violates research-first). The
  gate + closure ship as `state_lifecycle.py`, installed as `lumen-state-lifecycle`; the
  audit-chokepoint ticket (§5#7) makes the gated path the only write path.
- No schema change. Everything uses existing columns; the partial UNIQUE on
  `(subject,predicate) WHERE valid_to IS NULL` is preserved by closing before inserting.
- No recall-path change. `state_facts` feeds recall only as subject vocabulary
  (`lumen_recall2._subjects_re`); closure sets `valid_to` on rows but removes no subject,
  so the golden-set bench is unaffected by construction.

## Migration order (deploy, via install.sh)

1. `seed-registry` against live DB → `/home/lumen/minerva/verify/registry.yaml`.
2. `backfill --dry-run`, eyeball the report, then `backfill --apply`
   (writes lifecycle rows, then sibling closure for decommissioned/retired subjects).
3. `state-regen` to re-render projections.
4. thoth: do **not** hand-close `status=OFFLINE`; the wq#1608 reconciler (or an operator
   with live evidence) runs `close-on-up thoth --source-ref <probe>`.
