# wq#1613 — verifier registry + `lumen_verify` sweep

Source: `research/MINERVA_STALENESS_REVIEW.md` §2.2 (no probe for 12 predicate families;
oldest status-class facts 67/67/61/61/58 days unverified), §3.3 (registry design), §3.4
(provenance rules). Fixes ranked items #6 and #7 from §4, landed together per the ticket.

## What this adds

- `registry.yaml` — one file, deployed to `/home/lumen/minerva/verify/registry.yaml`,
  shared predicate-class list with wq#1632 plus the `probe` / `verify_interval_days` /
  `source_kind` keys wq#1632's loader already tolerates unknown-keys for.
- `lumen_verify.py sweep` — selects open facts whose `evidence_age >
  COALESCE(fact.verify_interval_days, registry interval, class default)` (status 3d,
  config 14d, identity 90d), runs the due probe once per `(probe, subject)` batch, and
  reconciles the result.
- Migration (additive, idempotent): `state_facts.source_kind` / `verify_recipe` /
  `verify_interval_days` / `contradicted_at` / `derived_from`, and a new
  `fact_verifications` audit table — MINERVA_STALENESS_REVIEW.md §3.1, migration 0009.
- Seven seed probes, each wrapping a tool that already exists: `tailscale_ip`
  (`tailscale status --json`), `fleet_inventory` (`fleet-inv get`), `service_state`
  (`systemctl is-active` over ssh), `node_storage` (`lsblk -J` / `dmidecode --type 17`),
  `os_kernel` (`uname -r`), `plug_map` (`plug-ctl status`), `endpoint_live` (HTTP GET).
- A probe that cannot observe a fact returns unknown and the sweep writes nothing (wq#2192):
  `os_kernel` answers only `kernel`; `service_state` probes only values that are systemd
  unit names and only ever confirms. `repair_clobbered_facts.py` undoes rows the earlier
  probes wrote (`uname -s` -> `Linux`, `systemctl is-active <status text>` -> `inactive`).

## Reconciliation contract (§3.2, §3.4)

wq#1608's reconfirm-on-match reconciler had not landed when this ticket was built, so the
contract is implemented directly in `reconcile()`:

- **match** -> `confirmed`: bump `last_confirmed_at`, clear `contradicted_at`.
- **mismatch, `source_kind != frank_stated`** -> `superseded`: close the old fact, open a
  new `observed` one.
- **mismatch, `source_kind == frank_stated`** -> `unknown`: the fact is never touched. A
  `fact_verifications` row records the conflicting observation and a `wq`/`aq` row is
  filed for a human to reconcile. This is the standing "never override what Frank told us"
  instinct, encoded in the schema instead of left to prose.
- **probe error or absent observation** -> `unknown`, no fact mutation (fail-open per
  probe — one broken probe never supersedes anything).

Every path writes an append-only `fact_verifications` row, so "checked and matched" and
"never checked" are now distinguishable (§2.3).

## Running it

```
lumen-verify sweep --dry-run --json   # report only, no writes
lumen-verify sweep                    # live: same logic, then commits
```

Deployed on `lumen-state-verify.timer`, `06:00 UTC` (before the 13:00 brief, after
overnight work), per §3.3.

## Out of scope here

- The generalised contradiction scanner (#2/#5, wq#1609) and `LIVE_FLEET_STATE` staleness
  rendering (#5, wq#1612) are separate tickets.
- `narrative` and `price`/market predicates have no class default and are never swept
  (§3.9) — prose lifecycle and `VOLATILE_HALFLIFE` own those respectively.
