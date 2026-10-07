# wq#3537 — probe-storage-integrity FAIL since 2026-10-06 23:07 PDT

## Root cause

`storage_probe.py` `assert_overlay_sync()` escalates `manifest:overlay-drift`
whenever `apply_overlay.build(overlay, manifest)` differs from the manifest.
The only drift was:

    meta.updated: '2026-10-07' -> '2026-08-13'

`manifest_add_node.py` stamped `meta.updated = today` when it added horus
(backup `storage_manifest.yaml.bak.20261007_044302_add_horus`, 04:43Z), but
`build()` gives `meta` to the overlay wholesale. Every node add therefore left
drift that only a hand-run `apply_overlay.py --apply` cleared. The esc row went
past the 45-minute SLA and `sla_sweep` held the probe at FAIL (`esc=1 sla=1`).
The farida add on 2026-09-24 had the same shape and was cleared by the
2026-09-28 `--apply`.

A `--replace` re-add hit the same bug in a second way: the fresh survey entry
overwrote the node's overlay `node_overrides` (e.g. the isis `max_watts`
recalibration). That also showed up as drift.

## Fix (live, `/home/lumen/agents/storage-audit`, commit `b2a422a`)

`manifest_add_node.diff` here is that commit. `add_entry()` sets the node's
entry and returns `apply_overlay.build(overlay, manifest)`, so the manifest it
writes is already in sync with the overlay. It no longer stamps `meta.updated`.
The `.bak.<ts>_add_<node>` backup still records when the add happened.

Live remediation: `apply_overlay.py --apply` (backup
`storage_manifest.yaml.bak.20261007_072451_pre_overlay_apply`).
Rollback: `git -C /home/lumen/agents/storage-audit revert b2a422a`.

## Evidence

- `test_add_node_overlay.py`: 19/19 pass with `test_parked_set.py` +
  `test_plug_autoclear.py`. With the pre-fix add logic swapped in, the 3 drift
  tests fail (fresh add, `--replace` calibration, committed `overlay.yaml`
  fixture). The 3 invariant tests still pass.
- `apply_overlay.py --check` before: 1 drift. After: `overlay in sync`.
- `storage_probe.py --run` 07:25Z: `AUTOCLEAR resolved 1 stale row(s):
  manifest:overlay-drift`, then `PASS storage-integrity: 12/16 nodes ... esc=0
  sla=0 critical=False`.
- `sentinel_probes.py`: `PASS probe-storage-integrity`, `EDGE-CLEAR ...
  resolved 2 stale aq row(s)`.

## Not this ticket

- unreach farida, apep, gameboy, seshat: real WARN, reported correctly.
- horus: the first collect hit `no ssh mapping (inventory + legacy)` and the
  re-probe 15 s later resolved. `manifest_add_node`'s step 2 ("add to the SSH
  map") hasn't been done, and the inventory lookup is flaky. Filed separately.
