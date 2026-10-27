## wq#4728 — scope the background-task COMPLETION detector to the CLI's notification channel

wq#4726 verify-passed wq#4707 and CONTRADICTED its done claim: `BackgroundTaskWatch`'s
completion side (`_BG_COMPLETE_RE`) matched **any** text in **any** user-frame content
block — tool_result or not — with no `tool_use_id` linkage, unlike the start side's
`tool_use_id not in self._bg_ids` gate. An ordinary foreground tool result whose text
merely says "background task … completed" (the shipped test file and its fixture both
contain that string; so does reading a ticket or a test log, which fleet doctrine
mandates) cleared a **real** pending task, reverting the session to the short
`POST_RESULT_GRACE_S` and killing it `post_result_stuck` with work outstanding — the exact
failure mode wq#4707 was filed to close.

### Fix (`harness/fworker.py:3089-3208`)

A completion is now read only from the CLI's own notification channel:

1. **Structured frame** — `{"type":"system","subtype":"task_notification","task_id":…,
   "status":"completed|failed|stopped"}`, no text matching at all. Prior art:
   anthropics/claude-code#104943 shows this frame in a real stream-json session, and
   CodeBuddy's stdio-protocol doc (aligned to Claude Code 2.1.220) states it is emitted
   exactly once per task at its terminal state and that completion must not be derived
   from any other event. wq#4707 had no handler for it.
2. **Injected text turn** — a non-tool_result user block carrying `<task-notification>` or
   the fallback phrasings (wq4686's captured shape).
3. **The tracked call's own tool_result** — `tool_use_id in self._bg_ids` only; the mirror
   of the start side's gate.

Plus: `_complete()` no-ops while `pending == 0`, so a stray match cannot bank credit a
later real start then silently spends (`pending = started - completed`) — a worker's own
dispatch prompt quotes this phrasing, this ticket's included.

Over-counting starts stays deliberately generous (safe: it only stretches the grace for a
live process, still bounded by `POST_RESULT_BG_GRACE_S`); the docstring now describes what
the code actually does instead of claiming unearned strictness.

### Tests — `harness/tests/test_wq4728_bg_complete_scope.py` (11)

Both wq#4726 repros, at unit and `run_child` level, plus the structured-frame channel and
a probe whose input is the committed wq4707 fixture's own bytes delivered as a Read
result (wq#2630: outside the designed-for population, pinned to an immutable file).

The probes can fail: against pre-fix `fworker.py` (2ba0c8fb, staged at
`/tmp/fworker-wq4728/prefix/`) **7 of 11 fail**, including the integration repro
(`bg_tasks_pending == 0`, killed at the short grace). After the fix:

```
harness/tests/test_wq4728_bg_complete_scope.py + test_wq4707_bg_grace.py :: 22 passed
test_wq992_done_watch + loop_break_integration + classifier
  + tool_ceiling_reissue + wq1284_spawn_hardening                       :: 82 passed
```

wq#4707's own 11 tests pass unchanged — its fix re-verifies, now without the hole.
ISSUE-LOG row 1612.
