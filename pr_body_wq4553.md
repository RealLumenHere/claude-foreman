## What

Stage a rewired `context_builder.py` (under `ops/wq4553-context-builder-live-roster/`,
building on wq#4537's hand-prose-preserving version) that reads FLEET DEVICES (roster)
from live `lumen_state.py` state facts and RECENT EPISODES from minerva.db
`archival_notes`, instead of frozen PG `fleet_devices`/`agent_episodes`. Decisions stay on
PG `fleet_decisions`, already filtered to non-superseded rows.

## Why

wq#4539 phase 1 (design/wq4539_one_render_one_truth.md): `claude_memory.md`'s roster and
episode sections render from PG tables that have taken 0 writes since 2026-10-08 — the
live truth moved to minerva.db and the render never followed. PG `fleet_devices` is wrong
for six nodes (stale status text); four of them are retired Macs that PG still shows as
available/offline.

## How

- `roster()`: shells `lumen_state.py current`, parses `subject.predicate = value
  (since DATE)` lines for `role`/`status`/`tailscale_ip`/`lan_ip`, excludes any node whose
  role or status text starts with "retired"/"decommissioned" (case-insensitive). The
  parser regex drops the end-anchor the design doc's reference implementation
  (`fleet_context.py`) uses, because live output always carries a trailing confidence tag
  after the date that anchor rejects — verified 0/1244 live lines match with the anchor.
- `episodes()`: reads minerva.db `archival_notes` (`kind IN ('code-ship','session')`,
  excluding revoked/superseded/merge-absorbed rows), most recent 40.
- Decisions/facts sections unchanged — phase 0 (QW5) already set
  `status='superseded'` on the two wrong rows, and the existing `status='active'` filter
  already excludes them.
- Staged, not applied: `/home/lumen/minerva` is walled off from this BUILD lane
  (wq#1719/wq#900) and `minerva/memory/` is untracked there, so there's no PR path for the
  live file (same wall wq#4537 hit). `install.sh` deploys it from a session with minerva
  write access, after a fresh `pg_dump` backup.

## Testing

- `python3 -m pytest -q ops/wq4553-context-builder-live-roster/test_live_roster.py` → 7
  passed, using a pinned immutable fixture of real `lumen_state.py current` output
  (`fixtures/state_sample_2026-10-23.txt`) plus a synthetic `archival_notes`/`note_merges`
  db for the episodes filter.
- Design doc's probe (a)+(b), replayed live against the real fleet (not a fixture):
  `roster()` called twice in one process returns the identical 30-host list both times
  (byte-stable — probe a); khepri/nephthys/hathor/isis (all four retired Macs, present in
  PG `fleet_devices`) are absent both times while thor/sekhmet/ptah/anubis are present
  (probe b). `episodes()` returns 40 real rows.
- Hand-prose round trip manually replayed against this file: the region survives
  byte-identical across two `--out` regens (wq#4537's contract, unbroken by this change).

## Risk / rollback

Read-only on PG and on minerva.db (no schema or data changes, no writers added). The only
live-system change is replacing one file (`minerva/memory/context_builder.py`) via
`install.sh`, which keeps a dated `.bak` copy and takes a fresh `pg_dump` first; rollback
is restoring that `.bak` copy. Nothing in this PR itself touches a live system — it ships
the staged bundle only, same as wq#4537.

## Findings logged along the way

- `prompt-forge/fleet_context.py`'s state-line regex (not touched by this PR) currently
  matches 0 live roster lines for the reason above — logged to `kb/ISSUE-LOG.md` #1534 for
  a follow-up ticket, since fixing it is out of this ticket's scope.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
