# wq#2048 — verify-pass of wq#2047 (lane_cap counted as structural skip, PR #662, commit b8a8065)

**Verdict: CONFIRM** — the "stall" was 12 pilot tickets refused by the daily lane cap, and the fix stops counting them as dispatchable. No contradiction, no escalation.

## What was run

| check | result |
|---|---|
| `pytest dispatcher/tests/test_dispatch_recover.py -k "structural or lane_capped"` | 3 passed |
| `pytest dispatcher/tests/test_dispatcher.py -k lane_cap` (cap + day-rollover reset) | 2 passed |
| live `dispatcher.log`, pass 20260919T070713Z (post-fix, b8a8065 is main HEAD) | candidates=130, 12 `lane_cap` skips (pilot, max_per_day=3), spawned=1, dispatchable=1 |
| live `dispatcher.log`, passes 06:5x–07:05Z (pre-fix) | dispatchable=12, spawned=0, matching the 12 `lane_cap` skips exactly |

## Findings

1. **Root cause matches.** `lane_cap` is emitted at one site only (`foreman_dispatcher.py:1247-1250`): `lane_counts[lane] >= max_per_day`, a per-UTC-day counter. It is not a concurrency cap, so nothing else can be hidden under the reason. The 12 skips in the live log are all `pilot`, `max_per_day=3 reached`.
2. **Fix is correct and minimal.** `lane_cap` added to `STRUCTURAL_SKIP_REASONS` (`:174`); `dispatchable = candidates - count_structural_skips(events)` (`:1535`) now excludes them. The pre-fix figure of 12 equals the 12 lane_cap skips, and the post-fix pass shows dispatchable=1 alongside spawned=1, so the watchdog no longer sees a stall.
3. **Lock (ticket part 1).** `lock_busy` appears 34 times in the whole log, last at 06:52Z, from overlapping manual and timer passes. `slock_acquire` (`:423-430`) takes a 30-minute TTL lease, so a wedged holder self-expires. Nothing to add.
4. **frank_active (ticket part 3).** The post-fix `pass_end` lines report `frank_active: true` with `orch-chat` / `orch-claude-chat` rows, so the detection works when orch sessions exist.

## Notes (not blocking)

- Trade-off: a lane whose `max_per_day` is permanently too low will now read as healthy. That is intended (the cap is configured refusal), but a "lane capped N days running" signal would be a separate ticket.
- The docstring list at `dispatch_health.py:19-20` does not name `lane_cap`; it was already an incomplete list of structural reasons.
- Full-file `test_dispatcher.py` exceeded 120s on this node, so only the targeted subsets above were run.
