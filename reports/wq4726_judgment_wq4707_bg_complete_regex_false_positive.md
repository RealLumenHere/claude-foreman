# wq#4726 — verify-pass on wq#4707 — CONTRADICTED

Verify ticket: wq#4726. Shipped ticket: wq#4707
(`fix(fworker): don't kill a worker waiting on its own background task`,
commit 2ba0c8fb3cecf2f15defb887aaa5032a7b285d9f, PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/2514). Author model:
claude-opus-5. Verifier: claude-sonnet-5@high, fresh instance.

## What the PR claims

`fworker.run_child`'s post-result watchdog (§C9) kills a session
`POST_RESULT_GRACE_S` (20s, default) after a terminal `result` frame on the
theory that no more work is coming. wq#4707's RCA
(`reports/post_result_stuck_rca.md`) found this false whenever the agent has
a background task outstanding (`run_in_background=True` Bash, an Agent/Task
call, or the CLI's own auto-backgrounded long command) — the CLI holds the
session open to re-deliver that task's completion as a new turn, and the
20s grace killed the worker first. The fix adds `BackgroundTaskWatch` to
track started/completed background work from the stream, stretches the
grace to `POST_RESULT_BG_GRACE_S` (900s default) while any is pending, and
clears `result_seen_at` on the next assistant/user frame so the countdown
rearms instead of firing on a stale timestamp.

## What verifies clean

All 11 shipped tests pass as committed:

```
harness/tests/test_wq4707_bg_grace.py :: 11 passed in 13.43s
```

This covers: Bash `run_in_background` start, the CLI's "running in background
with ID" auto-detection, Agent/Task default-backgrounded, ordinary foreground
work being ignored, the full wq4686 replay fixture, the long grace being
honoured while a task is pending, the short grace still firing with no task
pending (§C9 regression guard), the grace releasing once a real completion
notification lands, and a never-reporting task still being bounded by the
long grace rather than hanging forever. The `result_seen_at` rearm logic
(`harness/fworker.py:3320-3321`) is correct for the shapes these tests cover.

## Contradiction

`BackgroundTaskWatch`'s completion side (`_BG_COMPLETE_RE`,
`harness/fworker.py:3092-3097`) is not scoped to the CLI's actual
task-completion channel. Two of its three alternatives —
`\btask\b[^\n]{0,80}\b(?:completed|finished|failed|exited|has finished)` and
`background (?:task|command|shell|process|run)[^\n]{0,80}(?:completed|...)`
— are matched against **any** content block in a `user` frame
(`observe()`, `harness/fworker.py:3148-3155`), tool_result or plain text,
with no link to a tracked `tool_use_id`. Contrast the start side
(`_BG_AUTO_RE`), which only counts a start when
`tool_use_id not in self._bg_ids` — the completion side has no equivalent
guard at all. The class docstring claims detection is "strict on the
completion side"; it isn't: it fires on any matching substring, anywhere, in
any tool result.

This matters because the fleet's own doctrine is to read code, logs, test
files and prior tickets before acting — exactly the kind of text that
contains phrasing like "task ... completed" or "background task ...
finished" with no connection to a real outstanding task. The PR's own test
file is a working example: `harness/tests/test_wq4707_bg_grace.py:106`
contains the literal string `"Background task bash_1 completed"` inside a
`tool_result`-shaped fixture.

### Repro 1 — unit level (`BackgroundTaskWatch`)

```python
w = fworker.BackgroundTaskWatch()
w.observe({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "a", "name": "Bash",
     "input": {"command": "pytest", "run_in_background": True}}]}})
# -> started=1, pending=1 (correct: a real background task was started)

w.observe({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "READ_TOOL_ID", "is_error": False,
     "content": [{"type": "text",
                  "text": "Background task bash_1 completed\nsome other line\n"}]}]}})
# -> started=1, pending=0
```

The second frame is an ordinary tool result (e.g. a `Read`/`Grep` of a file
that happens to contain that phrase) — not a real completion. `pending` still
drops to 0.

### Repro 2 — integration level (`fworker.run_child`)

A child that starts a real, never-finishing background task
(`sleep 99999`, `run_in_background=True`), then does one piece of ordinary
foreground work whose tool result incidentally contains matching text, then
goes quiet:

```
killed_reason: post_result_stuck
elapsed: 1.5s          (post_result_grace_s=1.0, post_result_bg_grace_s=8.0)
bg_tasks_started: 1    bg_tasks_pending: 0   <- wrong: the real task never reported
```

The session is killed at the **short** grace (~1.5s) instead of being held
for the **long** one (~8s), because the incidental text flipped `pending` to
0. `state.bg_tasks_pending == 0` is reported even though the real background
task never sent a completion notification. This reproduces wq#4707's
original failure mode — a live worker killed as `post_result_stuck` while
real work is still outstanding — through a different trigger than the one
the PR's own tests probe.

Script: `/tmp/fworker-wq4726/repro_integration.py` (uses
`fworker.run_child` directly, same plumbing the shipped tests use).

### Probe-gap check (wq#2630)

None of the 11 shipped tests feed `BackgroundTaskWatch`/`run_child` a tool
result whose text incidentally matches `_BG_COMPLETE_RE` while an unrelated
real task is pending — every completion-side test uses the structured
`<task-notification>...</task-notification>` wrapper. The probe population
was drawn entirely from the designed-for shape (real notification text) and
could not have failed on this axis.

## Verdict

**CONTRADICTED.** The fix correctly handles the CLI's actual
`<task-notification>` wrapper and the Bash/Agent/Task start shapes its tests
cover, but the completion regex is unscoped enough that ordinary foreground
tool output can falsely clear a real pending background task, silently
reverting to the pre-fix short grace and recreating the exact failure mode
wq#4707 was filed to close. Recommend narrowing `_BG_COMPLETE_RE`'s two
broad alternatives to the CLI's literal notification shape (or gating them,
like the start side, to blocks not already matched against a tool call
outside `_bg_ids`) before trusting this gate on a live fleet.

Escalating per policy: opus-5@high judgment.
