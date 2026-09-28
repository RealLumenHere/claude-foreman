# wq#2971 — drills_575 accept drills all fail: the battery names wq#575

This ticket's implementation lives in a different git repo (`minerva`, hosted
at `http://100.121.17.5:3000/lumen/minerva.git`) than this worktree (`claude-foreman`):
`v4/probes/drills_575.py` is a minerva-tracked file, not a foreman one, and
this worker was dispatched into a foreman worktree with no `/home/lumen/minerva`
checkout and no `minerva` add-dir grant. `drills_575.py` is mirrored here
as-landed so this branch carries a citable copy of the real change; the
actual fix ships through minerva's own PR flow, same as wq#2901's PR #44/#45/#46
for the same file.

Landed: minerva PR #48 (branch `fix/wq2971-drills575-live-claim`,
commit `c4d26aa`) — `http://100.121.17.5:3000/lumen/minerva/pulls/48`.

## Problem

`SRC = "wq#575"` (drills_575.py:14, pre-fix) was hardcoded. The daemon's
identity gate (`kernel.agent_claims()`) requires a LIVE `status='claimed'`
row in queue.db naming the calling agent, and wq#575 closed in August — no
session can ever hold it — so every accept-path drill that wrote with
`source_ref=SRC` 403'd on identity alone, for any operator, forever: 11 of 22
drills (D12, D8, D13, D14, D15, D10, D5, D2_kill9, D3, D16, D17).

## Fix

`_mint_ticket(agent)` (drills_575.py:54) runs `wq add --lane memory --force`
then `wq claim <agent> --id <tid>` at import time, mirroring how
`fworker_example()` (D20, wq#2901) already derives a live ticket from
queue.db instead of hardcoding one — but owns the ticket outright rather than
borrowing one another worker happens to hold. `SRC` becomes `wq#<tid>`
(drills_575.py:88-89), falling back to the old `wq#575` only if minting
fails. D12's hardcoded expected `origin` string now builds from `SRC`
(drills_575.py:200) instead of the literal `wq#575`. The script releases the
throwaway ticket at exit regardless of whether the AGENT identity itself was
minted (drills_575.py:538-543).

A second, related bug surfaced only once identity stopped masking it: D13
and D15 write static content with nothing that ever supersedes or revokes
the resulting row, so a second run's identical content deduped to the first
run's still-live row under the daemon's I-3 no-op rule, and both failed
differently on re-run. Both now stamp the run's start time into their write
bodies so every run writes a genuinely new row.

## Verification

Run live against the real daemon (`http://127.0.0.1:8139`) and the real
`queue.db`/`minerva.db`, twice in a row after the fix (`/tmp/fworker-wq2971/out/drills_runA.json`,
`drills_runB.json`):

- All 11 originally wq#575-gated drills pass consistently on both runs.
- `D2_kill9_atomicity` and `D3_paired_restore` still fail on both runs, for
  reasons unrelated to identity and confirmed pre-existing (not introduced
  by this change or by wq#2901): `D2_kill9_atomicity`'s `audit_without_note=282`
  is `orphan_check()` counting `state_facts` writes dating back to
  2026-09-11 that its query was never scoped to exclude; `D3_paired_restore`
  fails on httpx INFO-log noise polluting a stdout scrape, unrelated to
  `source_ref`/identity (D3 never sets `SRC`). Filed as follow-ups: wq#3019
  (orphan_check scoping), wq#3020 (httpx stdout noise).
- Throwaway tickets mint/release cleanly (`status='open'`, `claimed_by=NULL`
  after release) in the `memory` lane, which `wq add` itself confirms the
  ticket dispatcher never scans (`lane 'memory' has no dispatcher/config.json
  entry`) — inert, matching the lane the original hardcoded wq#575 ticket
  lived in.
