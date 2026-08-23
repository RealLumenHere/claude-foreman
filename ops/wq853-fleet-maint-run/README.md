# wq#853 — fleet-maint-run: ordered rolling executor with G0-G7 gates

Phase 3 of the A11 maintenance-window design (`reports/opsaudit_a11_maintenance_window.md`
§4; that report itself lives on an unlanded branch, `b9a5331` on
`docs/wq761-ops-audit-a11-design-the-maintenance-win-*` — not yet on `main`). Phase 1
(fleet-maint-assemble, not built here) writes an approved `batch-YYYY-MM-DD.json` and
opens a `user`-lane wq ticket; Frank's reply is phase 2. `fleet_maint_run.py` is phase 3:
it walks that approved batch in the fixed order §5 mandates and runs every item through
G0-G7 before mutating anything, launched via `lumen-run` as a transient unit so a
Commander flap can't kill it mid-node (that launch wrapper is the caller's job, same as
any other `lumen-run start ... fworker.py run ...` invocation elsewhere in this repo —
this script is what runs *inside* the unit).

## Design choice: every gate's action is a batch-supplied shell command

This script does not know what "restart tailscaled" or "recreate the vaultwarden
container" means. Every gate's real action — apply, rollback, rollback dry-run, backup
precondition, health probe, up-check, failed-units listing, version ingest — is a shell
command string the approved batch supplies. `fleet_maint_run.py` only sequences, times,
registers paired restores, and enforces stop-on-first-failure. That is A11 §8's own
framing: A6's shared precondition runner (P1/P3/P4) doesn't exist yet, so this ships its
own verification over its own items — narrower and honest — rather than pretending to
share a runner that isn't built. When A6's runner exists, phase 3 should call it and
delete the local G2/G3/G4 checks below.

## Batch schema

```json
{
  "window_date": "2026-09-12",
  "preflight": {
    "mesh_quiet_cmd": "...",          // optional; abort whole run on nonzero
    "vault_ha_cmd": "...",            // optional; abort whole run on nonzero
    "dispatcher_stop_cmd": "...",     // optional
    "dispatcher_restore_cmd": "..."   // if dispatcher_stop_cmd ran, this ALWAYS
                                       // fires in phase 3b, success or failure
  },
  "items": [
    {
      "id": 1,
      "lane": "APP",
      "node": "gameboy",
      "component": "tailscale",
      "current_version": "1.98.10",
      "target_version": "1.102.3",
      "absent": false,
      "reboot": false,
      "consent": false,
      "requires_replication_lag_zero": false,
      "replication_lag_cmd": "...",
      "requires_backup": false,
      "backup_precondition_cmd": "...",
      "apply_cmd": "...",
      "pre_state_cmd": "...",
      "failed_units_cmd": "...",
      "rollback_dryrun_cmd": "...",
      "rollback_cmd": "...",
      "health_probe_cmd": "...",
      "up_check_cmd": "...",
      "ingest_cmd": "..."
    }
  ]
}
```

An item with no `apply_cmd` is a pure informational line (e.g. the assembler's
"apt tail — none" rows) and runs no gates. An `absent: true` item is a declared-absent
node (hathor, wadjet) — recorded as its own line, never silently dropped (A11 §5). A
`reboot: true` item needs `consent: true` already set in the approved batch (the live,
attended part of "attended start, unattended finish"); missing consent fails closed. It
also skips G2-G4 (there is no such thing as a rollback dry-run for a reboot — a bad
reboot is an incident, not an auto-rollback path) but still runs G1/G5/G6.

## Gate sequence (per item, in order)

| gate | what | on failure |
|---|---|---|
| G0 | batch file sha256 == the value on the approved wq ticket | abort **whole run**, no item touched |
| — | batch schema validation (every apply_cmd item has the fields its gates need) | abort **whole run** |
| G1 | pre-state + failed-units baseline captured | abort item |
| G2 | `requires_backup` → `backup_precondition_cmd` (A6 P1) | abort item |
| G3 | `rollback_dryrun_cmd` (A6 P3) | abort item |
| G4 | paired restore registered to `--registry` (A6 P4) | — |
| — | apply | auto-rollback, stop run |
| G5 | `health_probe_cmd` passes at T+60s **and** T+300s | auto-rollback, stop run |
| G6 | `up_check_cmd` + no new entries in `failed_units_cmd` since G1 | auto-rollback, stop run |
| G7 | `ingest_cmd` (best-effort), paired restore cleared | — |

G0 and schema validation run once, before any item and before preflight. Everything
else is per item. **Stop-on-first-failure is absolute**: the first item that doesn't
reach `OK`/`ABSENT` stops the run; every later item is recorded `NOT_ATTEMPTED` and
never touched.

## Ordering law (A11 §5, fixed in `NODE_ORDER`, not left to the batch)

```
gameboy, apep, thoth, seshat, sia   -- leaf nodes first
anubis, bes, isis                    -- service nodes
sobek                                 -- vault standby (the rehearsal)
maat                                  -- vault primary, last among remote nodes
lumen                                 -- runs the runner, always last
```

Within a node, reboot items always sort after non-reboot items. A non-absent item on a
node outside this list fails batch validation rather than being placed by guesswork.

## Phase 3b — RECORD, always runs

Runs even when G0 failed and no item was ever touched. In order: restore the dispatcher
timer if it was stopped (unconditional — that stop was itself a mutation with its own
paired restore per §5); best-effort `state-regen` + `lumen_episode.py commit` if any
item was attempted; write `<report-dir>/maint_window_<window_date>.md`; `ntfy`; and, if
`--wq-ticket` was given, `wq done <id> --notes "<status>; <report path>"` so the wq#759
evidence gate is satisfied regardless of whether the run completed or stopped early.

## Usage

```
python3 fleet_maint_run.py run \
  --batch /home/lumen/maint/windows/batch-2026-09-12.json \
  --expected-sha256 <sha256 from the approved wq ticket> \
  --wq-ticket 1234 \
  --registry /home/lumen/maint/state/paired-restores.json \
  --report-dir /home/lumen/foreman/reports
```

External tool paths (`wq`, `ntfy-send`, `state-regen`, `lumen_episode.py`) and the G5
wait times (60s / 300s) are overridable via `FLEET_MAINT_*` env vars — see the top of
`fleet_maint_run.py`.

## Tests

```
python3 -m pytest ops/wq853-fleet-maint-run/test_fleet_maint_run.py
```

Zero quota, no real fleet: item commands are real shell one-liners (`true`/`false`/
`echo`), and `wq`/`ntfy`/`state-regen`/episode-commit are pointed at a fake script that
logs its argv and exits 0.

## What this does not build

Per A11 §8: A6's shared precondition runner, container image digest pinning, and
anything about hathor/wadjet being unreachable. This also does not build phase 1
(`fleet-maint-assemble`) or the batch-JSON writer — it consumes an already-approved
batch and assumes the assembler (or a human, for the first window) produced one that
matches this schema.
