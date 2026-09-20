# wq#1782 — reconcile the contradiction-scan hits from wq#1780's first live run

## Result

The ticket premise (30 docs need correcting) does not hold. Re-scanning on
2026-09-20 gave 27 actionable hits; verified against `state_facts` and
`tailscale status`, the gameboy/nephthys address hits are scanner false
positives and the docs are correct. No doc was hand-edited because none was wrong.
The scanner was fixed instead; live rescan: **27 -> 18 actionable**.

## Root cause: predicate rename read as supersession

`state_facts` holds the address under two predicate names:

| fact | id | valid_to |
|---|---|---|
| `gameboy.ts_ip` = 100.118.56.59 | 51bb3543… | 2026-09-10T11:01:34 (bulk close, no successor under this name) |
| `gameboy.tailscale_ip` = 100.118.56.59 | 4bb1024e… | open |
| `nephthys.ts_ip` = 100.98.74.87 | 92cfe9df… | 2026-09-10T11:01:34 |
| `nephthys.tailscale_ip` = 100.98.74.87 | 2b08d3f6… | open |

`tailscale status` agrees (gameboy 100.118.56.59 offline 7d, nephthys 100.98.74.87
stale tagged device). `build_dead_value_index` only compared a closed fact with
the open fact of the SAME predicate, so the live address was indexed as dead and
every doc quoting it was flagged (FLEET_MAP.md:24,279; gameboy.yaml:9;
LIVE_FLEET_STATE.md:18,26; claude_memory.md:4212; 2 archival_notes rows).

## Fix

`ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`
(`build_dead_value_index`): a self-identifying, non-common-word value that is still
open under any predicate of the same subject is not dead. Bare labels (`active`)
are not rescued, and a different subject holding the value does not rescue it.
Tests: `test_lumen_contradiction_scan.py` (3 new), 54 passed.

## Verdict per remaining hit (18 actionable + 1 info)

| hit | verdict |
|---|---|
| FLEET_MAP.md:271 khepri.power_plug P1 | false positive: the row already carries Frank's 2026-08-22 correction; left alone |
| wadjet.yaml:6 p12.status (info) | informational, left alone |
| claude_memory.md:24, 5383 pbox retired -> DECOMMISSIONED | real but `claude_memory.md` is proposal-only (instinct 7); candidate below |
| claude_memory.md:56, 4229 | `ollama_version='0.32.6'` / `service_state` hits whose "current" is verifier garbage (below), not doc drift |
| 12 `archival_notes:*` rows | historical note rows, handled by the scanner's own `--apply` tier on the daily timer, not by hand edits to the DB |

Hits from the ticket that had already cleared before this run: hathor
(`apep.yaml:28`), `bes.yaml:7`, llama33-local rows.

## Proposal for minerva/dream (not applied)

`claude_memory.md:24` and `:5383`: say `PBox DECOMMISSIONED (2026-08-28)`
instead of `retired`. (`state_facts` pbox.status: retired 2026-06-07..07-05,
DECOMMISSIONED open since 2026-08-28.)

## Separate defect: state-verify clobbered live facts (split out)

At 2026-09-12T06:00:14 the wq#1613 verifier closed `hathor.status`, `bes.status`,
`sia.status` and wrote `inactive`, and wrote `ollama_version='Linux'` for
isis/lumen. `bes.status` RESTORED -> `inactive` and `ollama_version` = `Linux`
are wrong current values and are why several hits show a nonsense "current"
column. Filed as its own ticket; not fixed here.

## ISSUE-LOG row (guard.sh refused the Edit on /home/lumen/kb, wq#1719 wall)

| 483 | wq#1782 (2026-09-20): 27 contradiction-scan hits on gameboy/nephthys `ts_ip` and others reported as doc drift | `ts_ip` vs `tailscale_ip` predicate rename: the closed `ts_ip` fact had no same-predicate successor, so the still-live address was indexed as dead | scanner fix in `build_dead_value_index` + 3 tests, rescan 27 -> 18; docs untouched (correct); verifier clobber (`ollama_version='Linux'`, status `inactive`) ticketed separately | FIXED (scanner) / OPEN (verifier) |
