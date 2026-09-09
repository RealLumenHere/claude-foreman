# wq#1564 — LPP brain quality: cerebras-oss-120b vs qwen36-local

Session: fworker build lane • 2026-09-09

## Era boundaries (from the drop-in history, not guessed)

- **qwen-local era**: 2026-08-23T09:44:17Z → 2026-09-09T06:36:08Z.
  Start pinned by the `fix(brain): bound every brain call with a rung ladder`
  commit (86844df) whose own comment states "Primary is qwen36-local (Isis,
  ONE decode slot — wq#898)" — confirming qwen36-local was already the live
  `LPP_BRAIN_PRIMARY` at that point. `wq#1079`'s notes (done 2026-08-28)
  confirm PRIMARY stayed on qwen36-local through the Cerebras billing outage
  (wq#1047, 2026-08-19→2026-09-08) and only the escalate/actRetry/summarize
  roles briefly drifted onto paid deepseek-v4-flash before being corrected to
  mistral-small.
- **cerebras era**: 2026-09-09T06:36:08Z → now. Boundary is the `startedAt` of
  `run-summary-live-agent-1788935768505.json`, the first run launched after
  `/etc/systemd/system/lumen-pokemon-live.service.d/30-brain-env.conf` was
  rewritten today to set `LPP_BRAIN_PRIMARY=cerebras-oss-120b` (comment:
  "Reverts the wq#1079 austerity ... now runs on a DEDICATED scoped key").

## Confound: story progress is flat across BOTH eras

`runtime/milestones.json` shows the last new milestone flag (`EVENT_GOT_NUGGET`)
first-seen **2026-08-15** — before either era in this comparison starts.
`runtime/sentinel_state.json` (generated 2026-09-09T07:03:33Z) confirms:
`storyScore=10, badges=1, mode=FIGHT_LOSS_LOOP, lastAdvanceAt=2026-09-02T07:12:03Z,
frozenHours=167.86`. The sibling ticket wq#1162 (done 2026-09-02) fixed a
stall-*ledger* bug (false "advanced" credits masking the live stall), not the
underlying gym-2 fight-loss loop itself — gameplay has not produced a new
badge, story-score tick, or milestone since 2026-08-15, spanning the entire
qwen era and the first ~47 minutes of the cerebras era. **story_score
delta/hour, furthestRank progress, and newTiles/hour are therefore 0 for both
brains over this window** — not a brain-quality signal, a pre-existing
gym-2 leveling stall that predates this A/B entirely.

## What IS measurable: brain/infra operability

Source: `events/run-summary-*.json` (per-run turn counts, reboot counts,
`autoCalls.brain_breaker` trip counts), aggregated by era.

| metric | qwen (all runs, 306.9h) | qwen (excl. crash-loop outliers\*, 62.8h) | cerebras (0.78h, 1 run) |
|---|---|---|---|
| turns | 4555 | 3142 | 452 |
| turns/hour | 14.84 | 50.04 | 579.0 |
| reboots | 10978 | 156 | 1 |
| reboots/hour | 35.77 | 2.49 | 1.28 |
| brain_breaker trips | 67026 | 937 | 6 |
| brain_breaker/hour | 218.4 | 14.92 | 7.69 |
| whiteouts | 0 | 0 | 0 |

\* 11 qwen runs (2026-08-27→2026-09-05) show >100 reboots each, up to 5637
reboots / 33827 brain_breaker trips in one 88.7h run
(`run-summary-live-agent-1788616502243.json`, 6 real turns produced). These
are crash-loop windows tied to the qwen36-local single-decode-slot / brain
budget issues (wq#1079, wq#1066) rather than isolated flukes — they recur
across five separate weeks of the qwen era, so excluding them likely
*understates* qwen's real operational cost rather than overstating it.

Even against the generous "healthy qwen" subset, the one cerebras run so far
is markedly more stable: ~11.6x the turn throughput, ~2x fewer reboots/hour,
~2x fewer brain-breaker trips/hour, and zero multi-hour crash-loop episodes.
`retry_ledger.json`'s cumulative `boots` counter (1640) and `gameplayContinues`
(10988) corroborate the reboot volume — consistent with the qwen-era
aggregate above, not with a healthy steady state.

## RECOMMENDATION

**Keep cerebras-oss-120b as primary.** The operability data (turn throughput,
reboot rate, brain-breaker trip rate) is unambiguous in cerebras's favor and
qwen36-local's single-decode-slot constraint produced repeated multi-hour
crash loops across the qwen era. However, this ticket cannot certify "plays
better" in the story-progress sense the ticket asks about: both brains show
zero story_score/badge/newTiles movement across the full window, because the
FIGHT_LOSS_LOOP gym-2 stall (open, unrelated to brain choice) has suppressed
all forward progress since 2026-08-15. Re-run the story-progress half of this
comparison once (a) the gym-2 stall clears and (b) the cerebras era has
accumulated wall-clock hours comparable to qwen's (currently 0.78h vs 62.8–
306.9h) — a single 47-minute run is not enough to trust for gameplay-quality
deltas even though it is enough to show the operability gap.
