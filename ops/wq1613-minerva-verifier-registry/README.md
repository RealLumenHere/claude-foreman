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
- Same rule for wq#2204: `endpoint_live` returns the fact value on a good GET (never the HTTP
  status), and `node_storage` only confirms a `ram`/`storage` value whose stated size matches the
  measured total (all DIMMs, whole disks) -- otherwise unknown. The repair script also closes the
  `verify:endpoint_live` (`200`) and `verify:node_storage` (first DIMM / byte sum) rows.

## Reconciliation contract (§3.2, §3.4)

The contract is owned by `lumen_state.report_observation()` (wq#1608), not by this script
(wq#2053). `reconcile()` only reports what a probe saw and never compares old to new, so every
write goes through the real `ingest()` and keeps its invariants: lifecycle-closed gate,
canonical predicate resolution, out-of-order guard and the `memory_audit` trail. A write the
gate refuses (for example an operational fact of a decommissioned node) is logged as `unknown`
with the refusal in `detail`. Outcomes:

- **match** -> `confirmed`: bump `last_confirmed_at`, clear `contradicted_at`.
- **mismatch, `source_kind != frank_stated`** -> `superseded`: close the old fact, open a
  new `observed` one.
- **mismatch, `source_kind == frank_stated`** -> `unknown`: the fact is never touched. A
  `fact_verifications` row records the conflicting observation and a `wq`/`aq` row is
  filed for a human to reconcile. This is the standing "never override what Frank told us"
  instinct, encoded in the schema instead of left to prose.
- **probe error or absent observation** -> `unknown`, no fact mutation (fail-open per
  probe — one broken probe never supersedes anything).
- A superseding row gets `verify_recipe` set to the probe id afterwards, because `ingest()`
  does not carry it and lumen_state reads a row without one as unverifiable.

Every path ends in an append-only `fact_verifications` row (written by `ingest()` for confirmed, superseded and frank_stated conflicts, by `reconcile()` for unknown), so "checked and matched" and
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

## Deployed 2026-09-20 (wq#2203)

wq#2202 found `install.sh --verify` exiting 1: `/home/lumen/bin/lumen-verify` and
`/home/lumen/minerva/verify/registry.yaml` still held the pre-wq#2192 probes, so the
`lumen-state-verify` timer kept clobbering `status`/`*_version`/`os` facts. `install.sh` was
run as a detached `lumen-run` unit (28 tests passed, `repair_clobbered_facts.py --apply`
repaired 0 facts, units installed, timer enabled). `install.sh --verify` now prints
`VERIFY OK`. The closing `sweep --dry-run --json` shows `fleet_inventory` rows as `unknown`
(never mutating): `probe_fleet_inventory` passes `--json` to `fleet-inv get`, which rejects
it (`lumen_verify.py:194`, ISSUE-LOG #500).

## Redeployed 2026-09-20 (wq#2208)

wq#2207 found the wq#2203 deploy stale: wq#2204's e09ca18 (endpoint/storage probes no longer
clobber `url`/`port`/`ram`/`storage` facts) landed after it, so the live `lumen-verify` was one
commit behind and `install.sh --verify` exited 1. Once wq#2206 confirmed wq#2204, `install.sh`
was rerun as a detached `lumen-run` unit (42 tests passed, `repair_clobbered_facts.py` repaired
0 facts, exit 0) and `install.sh --verify` printed `VERIFY OK` (also as a unit, exit 0).

## Canonical sources (wq#5155, 2026-11-19)

Development of both installed files moved out of this bundle: `lumen-verify` lives in lumen-bin
(`/home/lumen/bin`, e.g. d7e8e8c wq#1688 identity, 8ce6a10 wq#5026 n/a) and `registry.yaml` in
minerva (`verify/registry.yaml`). The copies here had fallen 220+ lines behind, and `install.sh`
would have rolled both live files back. Now:

- `install.sh --sync` copies both from their repos' `origin/main` (commit the result).
- `install.sh` refuses (exit 3) while either copy differs from its canonical `origin/main`.
- `install.sh --verify` reports that drift as a MISS (vfile alone had called it "superseded").
- `ops/tests/test_wq5155_lumen_verify_canonical.py` fails when a copy drifts.
- `install.sh` no longer re-enables `lumen-state-verify.timer` when it is disabled (Frank gate
  wq#3750); `LUMEN_VERIFY_ENABLE_TIMER=1` overrides.

Two `test_lumen_verify.py` cases were updated for behaviour that landed in lumen-bin:
`hardware` now has a probe (node_identity), and `_ssh_argv` passes the remote command as one
quoted string.

## --verify agrees with the timer gate (wq#5173, 2026-11-19)

`install.sh` was run (53 tests, `repair_clobbered_facts.py` restored 3 `ram` facts, units
reinstalled byte-identical); the timer stayed disabled. `--verify` still failed with
`not enabled`/`not active` on the state the installer deliberately keeps. It now prints
`GATED lumen-state-verify.timer ...` for disabled+inactive, MISSes disabled+active (gate
bypassed), and with `LUMEN_VERIFY_ENABLE_TIMER=1` requires enabled+active as before.
Test: `ops/tests/test_wq5173_verify_gated_timer.py`. Enabling the timer stays Frank's call.
