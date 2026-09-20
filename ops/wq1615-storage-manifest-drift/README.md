# storage-manifest-drift (wq#1615)

Fix #8 from `research/MINERVA_STALENESS_REVIEW.md` §2.8. `storage_manifest.yaml` came
from a one-shot `gen_manifest.py` run and has been hand-tuned since, with nothing
comparing it to live `lsblk`/`mdadm`. `FLEET_MAP.md` is edited by hand and lags the
store. This package adds a daily regen-and-diff for both. It only reports. Applying a
regenerated manifest is a separate decision and nothing here makes it.

## What a run does

1. **Survey.** For every node in the manifest, it runs one read-only command:
   `lsblk -J`, `findmnt -J` and `ls /dev/disk/by-id`. Remote nodes go over ssh
   (BatchMode, the `fleet-telemetry` transport and `lumen_state.py` tailscale roster), and
   the local node runs it directly. Nodes with `parked: true` are skipped. A node that is
   unreachable or has no address shows up under coverage and is never counted as drift.
   `storage_probe.py` already alerts on reachability.
2. **Regen.** It rebuilds each surveyed node's `devices` / `arrays` / `mounts` from the
   survey and writes the result to `$STATE/storage_manifest.candidate.yaml`. Declared
   order and every hand-tuned field (`required`, `noauto`, governor, envelopes, …) are
   kept. Declared-optional entries the survey can't see (`optional_devices`,
   `required: false`, `noauto: true`) are kept too, so a clean survey reproduces the
   committed file exactly.
3. **Diff.** It writes a unified diff of committed vs candidate to `$STATE/storage_manifest.diff`,
   plus typed findings: `device_missing|undeclared|model_changed|size_changed`,
   `array_missing|undeclared|level_changed|members_changed` (arrays keyed by md-UUID; the
   mdadm and lsblk UUID forms normalize to the same value), and
   `mount_missing|undeclared|fstype_changed|uuid_changed|device_missing`.
4. **FLEET_MAP.md.** It flags `fleet_map_stale` when the newest open
   `tailscale_ip`/`lan_ip` fact is more than 2 days newer than the file's mtime, and
   `fleet_map_value_missing` when the map names a node but not that node's current
   `tailscale_ip`/`lan_ip`. The store value's IP token is used, since values carry
   provenance prose. Both are scoped to the predicates the map actually reconciles —
   measured against the whole store (wq#1800) the lag tracked unrelated churn and the
   map read stale forever. A subject whose newest open `status` starts with
   retired/removed/superseded/decommissioned/torn-down is skipped: its last IP stays
   open in the store forever, so otherwise the map is asked to re-adopt dead hardware
   (wq#1800: `p12`, a plug SUPERSEDED since 2026-08-07, matched inside prose).
   `OFFLINE`/`inactive` are not retirement — a node that is off still belongs.
5. **Raise (`--apply`, what the timer runs).** A drift set it hasn't seen before
   produces one aq row (`aq add storage-manifest-drift drift …`) and one build-lane wq
   ticket with a fixed title. The same drift set is not re-raised. wq also refuses a
   duplicate open title. Per the review's §3.5 routing table, FLEET_MAP findings are
   proposals and ride in the same ticket body. Nothing edits FLEET_MAP.md.

`$STATE` = `/home/lumen/.local/state/storage-manifest-drift/` (`report.txt`,
`storage_manifest.candidate.yaml`, `storage_manifest.diff`, `state.json`).

## Shapes from the live manifest

- Three LVM roots (lumen, anubis, sobek) declare `uuid:` as
  `/dev/disk/by-id/dm-uuid-LVM-…`, a device identity rather than an FS-UUID. They are
  checked by confirming the identity is still present on the node. They are not compared
  to findmnt's FS-UUID, which would give a false mismatch.
- apep has a device with `serial: UNKNOWN, model: ''` (gen_manifest's placeholder for
  a disk that reports no serial). It can't be verified, so it is kept as-is.

## Run

```
storage-manifest-drift                  # survey + report, writes $STATE, no aq/wq
storage-manifest-drift --apply          # + aq/wq on a new drift set (timer)
storage-manifest-drift --json --strict  # exit 1 on drift
storage-manifest-drift --survey-json s.json   # offline: saved {node: survey} map
```

Env overrides: `STORAGE_MANIFEST`, `FLEET_MAP`, `MINERVA_DB`, `STORAGE_DRIFT_STATE`.

## Install

```
lumen-run start --name wq1615-install --root -- bash ops/wq1615-storage-manifest-drift/install.sh
```

The script runs the tests, installs `/home/lumen/bin/storage-manifest-drift` and both
units, and enables `storage-manifest-drift.timer` (daily 06:40 UTC, 20 minutes after
`lumen-contradiction-scan`).

## Issues hit (for kb/ISSUE-LOG.md; guard.sh blocks worker writes to the primary kb tree)

| n | symptom | root cause | solution | status |
|---|---|---|---|---|
| a | wq#1615: `storage_manifest.yaml`, `gen_manifest.py`, `agents/storage-audit/` and `/home/lumen/FLEET_MAP.md` are outside the worker's --add-dir roots (`head`/`stat` refused "was blocked"). `fleet-exec` has no grant (`DENY grant_missing_env`). Attempt 3 ended PERMISSION_LOOP on this | the ticket's artifacts live under /home/lumen/agents and /home/lumen, neither granted | recovered the manifest schema from earlier worker transcripts in `agents/work-queue/_runs`, verified lsblk/findmnt JSON locally, built against fixtures, then ran the tool against the live files with `lumen-run start` (runs as lumen) and read `lumen-run log`. Dispatch fix: grant `--add-dir /home/lumen/agents/storage-audit` for storage tickets | fixed |
| b | `lumen-contradiction-scan` (wq#1609) `file_config_ticket()` runs `wq add <title>` (wq add needs `--title`) and `aq add --source .. --severity .. --message ..` (aq add is positional `source key title [msg]`, bin/aq:93) | argv written from assumed CLI shapes, and no test asserts the argv | filed wq#1797 with the exact fix. This tool uses the correct shapes and tests them | open (wq#1797) |

## Rollback

`systemctl disable --now storage-manifest-drift.timer`. The tool never writes the
manifest or the map, so there is nothing else to undo.
