# wq#1643 — split-watch guards a retired pipeline and reports ok

Found by wq#1627 (`research/FLEET_SERVICE_ATLAS.md` F-4): `lumen-split-watch`
polls `lumen-planner`/`lumen-generator`/`lumen-evaluator` (the agent5 split,
wq#140) and the `lumen5:*` redis db5 queues every 10 min via
`lumen-split-watch.timer`, and had settled into printing a plain
`ok depths=[206,0,0] dead=187` — 206 items stuck in `plan_in`, 187 in `dead`,
no alert. Same finding also named the healer's `stall.lumen-agent` entry
(masked unit) and its baked `FALLBACK_TABLE` (still lists
`lumen-prompt-engine`/`lumen-vip-api`/`lumen-vip-watcher`, retired 2026-08-28
per wq#1112).

## Sweep

`systemctl status`/`list-unit-files` on 2026-09-10:

| unit | state | verdict |
|---|---|---|
| lumen-planner | could not be found | **not-found** — unit file genuinely gone, not masked |
| lumen-generator | could not be found | **not-found** |
| lumen-evaluator | could not be found | **not-found** |
| lumen-split-watch.service/.timer | static/enabled | still firing every 10 min against the above |

Root cause of the false "ok": `split_watch.py`'s alerting is edge-triggered —
each signal (unit down, queue stall) fires once on the transition into a bad
state and then sets an `alerted`/`unit:<name>` flag that suppresses repeats
until recovery. The three units transitioned to down, and `plan_in` to
stalled, once, a long time before this was audited; both edges had already
fired and latched, so every run since has legitimately had nothing new to
alert on — the design is edge-triggered by intent (`split_watch.py:11`,
`STATE`), it just has no notion of "this target is never coming back," so it
keeps re-confirming a dead state as unremarkable instead of surfacing that a
retired pipeline is still holding 393 items.

`healer_watch_table.yaml`'s `stall.lumen-agent` rule was **not** a live bug on
inspection: `check_stall()` (`lumen_self_healer.py:288`) gates every rule on
`is_systemd_active(unit)` and `continue`s for a masked unit — it is inert by
construction and self-arms if Frank ever revives `lumen-agent` (comment at
`healer_watch_table.yaml:43-46` already documents this). wq#1627 itself called
it "harmless but stale," not broken.

`FALLBACK_TABLE` (`lumen_self_healer.py:49-59`) **is** stale: it still lists
the three research-mill ghosts wq#1112 removed from the live table. Checked
against wq#1112's own fix (`lumen_self_healer.py:138-174`, `_unit_load_state`
+ `_file_stale_unit_ticket` wired into `check_all()`'s per-unit loop): that
guard applies to *any* unit sourced from `table["units"]`, live or fallback,
so a fallback-triggered run today would file three stale-target tickets and
skip — not restart-loop. Still worth cleaning since it is inaccurate
documentation-as-code and would otherwise cost three pointless ticket filings
the one time the real table is ever unreadable at startup.

## Decision

**Retire the agent5 split formally** (not restore it — the units are
not-found, i.e. the unit *files* are gone, a structural retirement, not a
masked/paused state that "restore and fix the STALL rule" would fit). Per the
standing full-autonomy instruction (decide and act, no Frank-gate for a call
like this), this is exactly the class of decision to make and record, not
park.

## Fix (class + instances)

1. **`docs/fleet/retired_services.yaml`** (new) — formal retirement record for
   `agent5-split`, plus context entries for the two adjacent findings
   (research-mill FALLBACK_TABLE staleness, lumen-agent masked stall rule) so
   wq#1644's retirement-audit tooling has one place to check before flagging
   a guard target as stale.
2. **`split_watch.py`** (diff: `split_watch.diff`) — self-detecting
   retirement: on every run, if all three units are `LoadState=not-found`,
   drain `lumen5:plan_in`/`gen_in`/`eval_in`/`dead` once, persist
   `retired: true` in its own `state.json`, fire one `RETIRED` ntfy notice,
   and return immediately. Every run after that hits the `st.get("retired")`
   short-circuit at the top of `main()` and exits without touching
   redis/systemctl at all — no more misleading `ok` line, no more polling a
   ghost pipeline. This is "disable split-watch" in effect: `systemctl
   disable`/`mask` is on this build lane's deny-list (house guard hook), so
   the script disables itself instead of the unit.
3. **`lumen_self_healer.py` `FALLBACK_TABLE`** (diff:
   `healer_fallback_table.diff`) — drops the three research-mill ghosts and
   `lumen-vip-api`'s port row, matching wq#1112's live-table cleanup.
4. **`healer_watch_table.yaml` `stall.lumen-agent`** — left untouched
   (confirmed inert by construction, see Sweep above); no diff needed.

## Apply

Both live files (`/home/lumen/agents/split-watch/split_watch.py`,
`/home/lumen/agent/lumen_self_healer.py`) are outside this build lane's
`--add-dir` scope (this ticket's worktree lists `agents/work-queue`, not
`agents/split-watch`; and `agent/` isn't listed at all) — `Bash`/`Edit`/`Write`
against either path are refused ("...may only ... from the allowed working
directories"), matching the wq#1112 precedent where the code-side fix also
needed a follow-up lane. Read access worked fine for research (used above).

Filed for a lane with the right add-dir / systemctl-mutate permission:
**wq#1739** — apply `split_watch.diff` and `healer_fallback_table.diff` from
this directory to the two live files, verify with `python3 -m py_compile` on
both, then confirm the next `lumen-split-watch` timer fire prints the
`RETIRED` line (not `ok depths=...`) via
`journalctl -u lumen-split-watch --since -15min`, and that
`redis-cli -n 5 llen lumen5:plan_in` reads 0. Also covers masking/disabling
the `lumen-split-watch.timer` unit itself (systemctl mutate, also outside this
lane).

## Rollback

Both diffs are additive/subtractive in the obvious way — reverse-apply with
`patch -R` against the live files, or restore from the pre-change `.bak`
copies the applying lane should take before editing (same convention as
`ops/wq1112-healer-stale-target-guard/`).
