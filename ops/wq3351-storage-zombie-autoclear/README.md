# wq#3351 — probe-storage-integrity FAIL x634 (aq#1482)

## Root cause

`storage_probe.py --run` returns FAIL whenever `sla_sweep()` finds an open,
unowned, non-default storage aq row older than 45 min. The only such row was
**aq#1568 `seshat:zombie`** (UNREACH_DRAWING, high, unassigned, opened
2026-09-20T01:37Z, last bumped 2026-10-01T02:09Z, x521).

`seshat:zombie` fires only while seshat is unreachable. `aq_autoclear()` only
resolved rows whose node was collected over SSH that cycle, so this row could
**never** clear, even after seshat's plug (p6) read 0.0W in a fresh
`plugs.json`. The zombie check is evaluated from Lumen's own plug snapshot, not
over SSH, so the SSH-coverage gate was the wrong evidence test for it.

This happened before: wq#1982 cleared the same lockup (aq#1473 `hathor:zombie`)
with a manual `aq ack` and left the mechanism in place, so it recurred with seshat.

## Fix (live, `/home/lumen/agents/storage-audit`, commit `41b1f04`)

`storage_probe.diff` here is that commit's diff of `storage_probe.py`:

- `fresh_plugs()`: plug rows from `plugs.json`, `[]` when older than 15 min
  (factored out of `assert_watts`, same staleness rule).
- `plug_nodes()`: nodes with a **numeric** watts reading in a fresh snapshot.
- `aq_autoclear(..., plug_nodes)`: `<node>:zombie` / `<node>:watts` rows count as
  covered when the node is in `plug_nodes`, reachable or not. Every other key on
  an unreachable node (unreach, mount, RAID…) still stays open.

Backup: `storage_probe.py.bak.20261001_pre_wq3351` (committed alongside).
Rollback: `git -C /home/lumen/agents/storage-audit revert 41b1f04`.

## Evidence

- `test_plug_autoclear.py` (7 tests, hermetic tmp DB + tmp plugs.json):
  13/13 pass with `test_parked_set.py` on the patched file; 6/7 fail on the
  pre-fix file (the 7th covers the unchanged `assert_watts` behavior).
  Negative cases cover inputs outside the fix's scope: a stale snapshot, a
  missing/None watts value, and non-plug keys on the same unreachable node.
- Live `storage_probe.py --run`, 2026-10-01T16:25Z:
  `AUTOCLEAR resolved 1 stale row(s): seshat:zombie` →
  `PASS storage-integrity: 11/15 nodes, fixed=0 cleared=1 esc=0 warn=4 sla=0 … critical=False`.
- aq#1568 → `resolved`, note `auto-cleared: … (fresh plug reading)`.

## Still open (real outages, WARN/default, not a probe defect)

unreach: farida (no ssh mapping in inventory or legacy map), apep, gameboy,
seshat. They are reported correctly and no longer hold the probe at FAIL.
