# wq#4533 — verify-pass: wq#4522's judgment fix on wq#4507 (Textual streaming, PR#2385)

**Verdict: CONFIRM.** `limpet/agent_tui.py`'s thread-worker fix
(`_stream_reply`, a `@work(thread=True, group="agent-turn")` method) holds up
under adversarial probing beyond the shipped test suite. I could not falsify
wq#4522's claim that the UI thread no longer blocks on a streaming backend,
including on the exact failure mode (the quit binding) that wq#4521's
original report named but wq#4522's own tests never directly exercised.

Commit verified: `0b0e7da64c` (merged to `main`), PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/2385. Files:
`limpet/agent_tui.py`, `limpet/test_agent_tui.py`,
`reports/wq4522_judgment_textual_streaming_blocking.md`.

## What the diff does

Confirmed against `git show 0b0e7da64c --stat`: scoped to the streaming
integration point exactly as claimed — `on_input_submitted` no longer
iterates the backend itself; it delegates to `_stream_reply`, a thread
worker that iterates `AgentSession.reply_stream()` off the UI thread and
marshals every widget write back via `call_from_thread`. `_turn_in_flight` +
a disabled `#chat-input` serialize turns to one at a time. A backend
exception mid-stream is rendered (`agent: [backend error: ...]`), not fatal.
`run_readline` is untouched (confirmed: no `+`/`-` lines inside that
function in the diff). Matches wq#4522's own description; not re-litigated
further since the scoping claim is itself verifiable from the diff, not a
behavioral one.

## Verification performed

1. **Read the diff cold** (`git show 0b0e7da64c` against the pre-fix
   `89782767:limpet/agent_tui.py`), then wq#4521's and wq#4522's reports.
2. **Re-ran the shipped suite independently**, worktree copy, not the
   author's: `python3 -m pytest limpet/test_agent_tui.py -q` → **19 passed**,
   matching the ticket's claim exactly.
3. **Adversarial probe 1 — quit binding during an in-flight turn**
   (`/tmp/fworker-wq4533/probe_quit_while_streaming.py`, not committed —
   this specific scenario is absent from wq#4522's own two new tests, which
   check timer liveness and `press()` latency but never fire `ctrl+c` while
   streaming). Real `LimpetChatApp` under `run_test()`, backend = 4×0.4s
   chunks (1.6s reply). Result: `press()` returned in **0.111s** (matches
   wq#4522's own numbers), then `pilot.press("ctrl+c")` returned in
   **0.084s** with `app.is_running` immediately `False` — the quit binding,
   the exact thing wq#4521's original report said froze, stayed responsive
   mid-stream and worked on the first press. No hang.
4. **Adversarial probe 2 — thread lifecycle after quit**
   (`/tmp/fworker-wq4533/probe_quit_thread_leak.py`): same setup, but with
   `threading.excepthook` instrumented and thread count sampled 2s after
   quit, to check whether the backend worker thread becomes a zombie that
   keeps writing to a workspace whose app has already exited. Result: thread
   count returns to the pre-turn baseline (1) within 2s of quit (no leaked
   thread), zero exceptions through `threading.excepthook`, and
   `session.jsonl` shows a clean partial write (user turn + exactly one
   `agent_message` delta, `"a"`) — the worker stopped after the in-flight
   chunk rather than continuing to append indefinitely into a dead app's
   workspace.
5. **Adversarial probe 3 — is `_turn_in_flight` a real guard, or is the
   disabled `#chat-input` doing all the work?**
   (`/tmp/fworker-wq4533/probe_double_submit.py`): wq#4522's own "second
   submit while in flight" test drives both presses through
   `pilot.press("enter")` on an already-disabled `Input`, which may just be
   Textual's widget-level disabled-state filtering doing the job — in which
   case the `_turn_in_flight` flag would be untested dead code. Called
   `app.on_input_submitted(Input.Submitted(...))` directly, twice in a row,
   bypassing any widget-level filtering. Result: only the first ("first")
   user turn is recorded, and the final agent reply is the complete,
   non-interleaved `"first:afirst:bfirst:c"` — the flag itself blocks the
   second dispatch, independent of widget disabling.
6. **Checked the operator-note gotcha wq#4522 logged** (ISSUE-LOG row 1524,
   `python3 -I` silently drops user site → `textual` import fails → cryptic
   `AttributeError`): reproduced it myself on probe 1's first run before
   switching to plain `python3`, confirming the row is accurate and not
   re-logging it.

## Conclusion

Every angle I could construct against this fix — including the one most
directly tied to wq#4521's original complaint (the quit binding) and the
one that would show the turn-guard was theater (direct dispatch past the
disabled widget) — holds. **CONFIRM.** No escalation to judgment.
