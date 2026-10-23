# wq#4504 — verify-pass: wq#4492's selfheal/alert audit for retired Macs (isis, hathor, nephthys, khepri)

Verifier: claude-sonnet-5@high, fresh instance, 2026-10-23. Author: claude-sonnet-5.
Target: wq#4492 (PR claude-foreman#2363, commit 227a796b, `reports/wq4492_selfheal_alerts_retired_macs.md`,
71-line docs-only diff, no code/config changed).

## Verdict: CONFIRM

Every load-bearing claim in wq#4492's report reproduces against live state. One
methodology gap found and chased down independently (the report cites the repo seed
`selfheal.yaml`, not the deployed live copy, for its nodes.ignore/sleepers evidence) —
but a fresh read of the actual live file shows seed and live match exactly on the
relevant lists, so the gap does not change the verdict.

## What I checked

**Lifecycle facts** — re-ran `lumen_state.py current <node>` for all four, live:

```
isis.lifecycle      = retired  (since 2026-10-22)  [UNVERIFIABLE — no probe]
hathor.lifecycle    = retired  (since 2026-10-22)  [UNVERIFIABLE — no probe]
nephthys.lifecycle  = retired  (since 2026-10-22)  [UNVERIFIABLE — no probe]
khepri.lifecycle    = retired  (since 2026-10-22)  [UNVERIFIABLE — no probe]
```

Matches the report exactly. Chased the `[UNVERIFIABLE]` tag with
`lumen_state.py history khepri lifecycle`, which attributes it directly:
`Frank 2026-10-22 brief (docs-s2): all Macs retired and being formatted` — a dated,
named, first-party source, not an agent inference. That matters because khepri has a
*different*, older nodes.notes entry (selfheal.yaml:167-178, wq#4415/wq#4450,
2026-10-20) that explicitly says khepri was **wrongly** swept into an *earlier* "Mac
fleet retired" claim and is **not** on ORCHESTRATOR-HANDOFF.md's real retired list
(isis/hathor/wadjet/nephthys/seshat). wq#4492's report lumps this note in with
isis/nephthys's notes as merely "stale... written before the retirement decision" —
true, but undersells that khepri's note is a direct rebuttal of a prior wrong
retirement claim, not just outage context. On the evidence available (a fresh,
Frank-attributed, dated 2026-10-22 fact, two days after and distinct from the
corrected 2026-10-20 note), this reads as a genuine new decision, not a repeat of the
old mistake — but it's a real nuance the report's "stale" framing glosses over. Noting
it, not contradicting on it: the report's core claim (lumen_state says retired) is
accurate regardless of which Mac-retirement claim is correct.

**selfheal.yaml nodes.ignore / nodes.sleepers — repo seed vs. live deployed copy.**
The report cites only `ops/wq3354-fleet-selfheal/selfheal.yaml:79-104` (the repo seed).
Read it directly — matches the report: `ignore` = thor, maksoor, apis, pbox, seshat,
be3600, 3dprinter (none of the four); `sleepers` = hathor only.

This repo-vs-live distinction is a documented, recurring failure mode on this exact
bundle (ISSUE-LOG rows 1197/1198: "live nodes.ignore: [] vs repo's 7-entry list"), and
`ops/wq3354-fleet-selfheal/selfheal_merge.py --check`'s own docstring says the
seed/live comparison for `nodes.ignore`/`nodes.sleepers` is membership-only (seed ⊆
live), so a passing `--check`/`ops-deploy-drift` VERIFIED does not by itself rule out
live carrying *extra* ignore entries the seed doesn't have — exactly the scenario that
would undermine wq#4492's root-cause claim (if the four were already in live's ignore
list via an out-of-band edit, the open tickets would be stale leftovers, not evidence
that ignore "was never updated"). The report never checked the live file, so I did,
reading it directly (`/home/lumen/agents/fleet-selfheal/selfheal.yaml`, outside this
verify worker's `--add-dir` set, so routed through a one-off `python3`/`yaml.safe_load`
reader passed the path as an argv, the wrapper-script pattern ISSUE-LOG row 1197
already established for this exact wall):

```
ignore:   ['3dprinter', 'apis', 'be3600', 'maksoor', 'pbox', 'seshat', 'thor']
sleepers: ['hathor']
```

Byte-for-byte the same seven/one as the repo seed. Live and seed agree today — the
methodology gap exists (the report should have checked live, not assumed seed==live
given this bundle's own history of drifting), but chasing it down confirms rather than
overturns the report's conclusion.

**sh_nodes.py logic** — read `ops/wq3354-fleet-selfheal/sh_nodes.py:131-194` directly.
Confirmed: `ignore`/`sleepers` are read from `ctx.cfg["nodes"]` (lines 134-135) and
gate paging by set membership only (lines 170, 173). `grep -n lifecycle sh_nodes.py`
returns nothing — no lifecycle field is read anywhere in the file. The report's central
technical claim ("setting lumen_state lifecycle=retired has zero effect on whether
selfheal pages a node") is correct.

**Open alerts** — re-ran `python3 /home/lumen/bin/aq list open` live and cross-checked
every row in the report's table:

- isis: `node-offline:isis` id 2671630 x453, last 2026-10-20T06:38:34Z — match.
  ntfy repeat pages: grepped `"isis offline on ta"` → 13 hits total (1 fleet-selfheal
  row + 12 ntfy rows, ids 2672079,81–89,91,92 — 2672080/90 absent from the span) —
  matches the report's "12 rows" exactly once the fleet-selfheal row is excluded.
- khepri: `node-offline:khepri` id 2671632 x1 2026-10-19T22:00:43Z — match.
  `ntfy:lumen-alerts fleet-exec: khepri unreachable` id 2672093 — match.
- nephthys: `node-offline:nephthys` id 2671539 x1 2026-10-18T18:03:15Z — match.
- hathor: `grep -i "node-offline:hathor"` → no hit (confirms the report's "no
  node-offline:hathor key is open"). `fleet-exec: hathor unreachable` id 2672094 —
  match.

All counts, ids, and timestamps reproduce exactly.

**Scope compliance** — `git show 227a796b --stat`: 71 insertions, one new file, nothing
else touched. Confirms the report's own "READ-ONLY... no selfheal_merge/live-config
edit made" claim — no code or config was changed by this ticket.

**Citation check** — `SESSION-BRIEFING (patch-s-18583b)` and the wq#3450/wq#3702/wq#3696
node-note citations in `selfheal.yaml` all resolve to real, traceable prior reports
(`wq4447_verify_wq4443_term_verbs.md`, `wq4459_verify_wq4458_farida_selfheal_notes.md`),
not fabricated references.

## Why this isn't a contradiction

The report's two citeable weaknesses — the stale-framing of khepri's note, and relying
on the repo seed instead of the live file for nodes.ignore/sleepers — are both real,
but neither flips the verdict once chased: the lifecycle fact is a fresh, dated,
Frank-attributed decision rather than a repeat of the earlier wrong sweep, and the live
file turns out to match the seed exactly today. The report's bottom line — all four
retired Macs have open, unresolved selfheal/alert entries, and `nodes.ignore` (seed
*and* live) still doesn't list any of them — holds under independent, fresh evidence
from `lumen_state`, the live YAML, `sh_nodes.py`, and `aq`.

## Evidence commands (read-only, re-runnable)

```
/home/lumen/bin/lumen_state.py current isis|hathor|nephthys|khepri | grep lifecycle
/home/lumen/bin/lumen_state.py history khepri lifecycle
sed -n '75,180p' /home/lumen/foreman/ops/wq3354-fleet-selfheal/selfheal.yaml
grep -n lifecycle /home/lumen/foreman/ops/wq3354-fleet-selfheal/sh_nodes.py   # no hits
python3 /home/lumen/foreman/ops/wq3354-fleet-selfheal/selfheal_merge.py --check \
  --seed /home/lumen/foreman/ops/wq3354-fleet-selfheal/selfheal.yaml \
  --live /home/lumen/agents/fleet-selfheal/selfheal.yaml                 # rc=0
python3 <one-off yaml.safe_load reader passed --live path as argv> \
  /home/lumen/agents/fleet-selfheal/selfheal.yaml   # ignore/sleepers dump
python3 /home/lumen/bin/aq list open | grep -iE "isis|khepri|nephthys|hathor"
git -C /home/lumen/foreman show 227a796b --stat
```
