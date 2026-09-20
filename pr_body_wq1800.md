## What

Narrows two FLEET_MAP.md checks in the storage drift detector, and fills the one
real roster gap they surfaced.

- `fleet_map_stale` now measures lag against the newest open
  `tailscale_ip`/`lan_ip` fact instead of the newest fact in the whole store.
- `fleet_map_value_missing` now skips subjects whose newest open `status` starts
  with retired/removed/superseded/decommissioned/torn-down.
- `ops/wq1800-fleet-map-drift/apply.py` fills `/home/lumen/FLEET_MAP.md:22`'s
  lan_ip cell for be3600 (`VERIFY` -> `192.168.0.1 (grade B)`). Already run.

## Why

The checker raised a ticket with 3 findings: 1 real, 2 artifacts of scopes wider
than the reconciliation they drive.

`fleet_map_stale` compared the map's mtime to `max(valid_from)` over the whole
store. The newest open fact was `fleet-workstream frank_todo_order` — a todo
ordering FLEET_MAP.md never carries — so any store write at all made the map
"stale", and at a 2-day threshold it could never clear.

`fleet_map_value_missing` fired on `p12`, an Athom smart plug with no roster row,
SUPERSEDED since 2026-08-07, matched only inside a prose plug list
(`p1 khepri + p12 wadjet unmonitored`). A retired subject's last IP stays open in
the store forever — nothing supersedes a fact about hardware that is gone — so
the map was being asked to re-adopt dead devices on every run.

be3600's lan_ip was the real one. The store has held `192.168.0.1` since
2026-09-10 (grade B: inferred from apep's default gateway + ARP, not read off the
router), corroborated by apep's own roster row at `192.168.0.161` — apep sits
behind that router.

`storage_manifest.yaml` had 0 findings; the survey-regenerated candidate matches
the committed file exactly.

## How

`FLEET_MAP_RETIRED_STATUS` is a prefix-match tuple against the newest open
`status` fact per subject. `OFFLINE` and `inactive` are deliberately absent — a
node that is off still belongs on the roster, and be3600 is OFFLINE, so
suppressing those would have hidden the one real finding here.

`apply.py` runs via `lumen-run start -- python3 apply.py` (as lumen, the file's
owner): FLEET_MAP.md sits at `/home/lumen` root, outside every worktree and
`--add-dir` root (ISSUE-LOG #322). It backs the file up, asserts the row and the
cell's prior contents before touching anything, edits exactly one table cell, and
is a no-op on a second run.

Full per-finding triage, including what was deliberately left alone, is in
`ops/wq1800-fleet-map-drift/README.md`.

## Testing

    $ python3 -m pytest ops/wq1615-storage-manifest-drift/ -q
    29 passed

Two new cases: a retired subject's missing IP is not drift while an OFFLINE one
still is, and the stale check ignores non-roster predicate churn.

Live `check_fleet_map` against the real map and store, after `apply.py`:

| detector | lag | findings |
|---|---|---|
| patched (this PR) | -9.0d | *(clean)* |
| installed (stock) | -0.2d | `p12 fleet_map_value_missing lan_ip 192.168.8.111` |

The stock detector's -0.2d is only the edit's own mtime bump; it refires in two
days on unrelated store churn.

## Risk / rollback

The detector is diff-and-report only — it raises aq/wq rows and never writes
either file, so a wrong suppression loses a signal, it does not corrupt state.
The suppression is narrow: prefix match on an explicit retirement vocabulary,
with the down-but-present statuses held out and covered by a test.

Rollback: revert the commit. The FLEET_MAP.md edit is separate and reverts by
restoring `/home/lumen/FLEET_MAP.md.bak.20260920_033801`.

Refs: wq#1800, wq#1615
