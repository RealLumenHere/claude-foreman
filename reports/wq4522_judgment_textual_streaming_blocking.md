# wq#4522 — judgment: wq#4521's CONTRADICT on wq#4507 (Textual TUI blocks the event loop)

**Decision: (a) fix now.** `limpet/agent_tui.py`'s Textual frontend now iterates the
backend on a thread worker, so the UI thread never waits on a chunk. Option (b) (accept
as a known gap, gate the limpet-session integration ticket) was rejected: the fix is 40
lines at one integration point, and deferring it means the *next* ticket must land a
real network backend and a concurrency refactor in the same change, with the first
symptom being an apparently-hung TUI in front of an operator.

wq#4521's finding is upheld in full — independently reproduced here against the
pre-fix module, with sharper numbers (below).

## What changed

`limpet/agent_tui.py` only, scoped to the streaming integration point. `AgentSession`,
the readline fallback, the event-log shape, and the status/tool panels are untouched —
wq#4507's diff was not re-implemented.

- `on_input_submitted` no longer iterates the reply. It writes the user line, shows
  `agent: ...`, and hands off to `_stream_reply`, a `@work(thread=True)` worker
  (`limpet/agent_tui.py:253`). Chunk-by-chunk widget updates cross back to the UI thread
  via `call_from_thread`.
- One turn at a time: `_turn_in_flight` plus a disabled `#chat-input` for the duration.
  `AgentSession.turns` and `notes/session.jsonl` are a single append-only stream with no
  turn ids (spec §6), so two concurrent replies would interleave their deltas. The input
  re-enables and refocuses in `_finish_turn`.
- A backend that raises mid-stream is rendered, not swallowed and not fatal: the partial
  text plus `agent: [backend error: <type>: <msg>]` (spec §5 failure rendering). Pre-fix
  the exception propagated out of the message handler.
- `run_readline` is deliberately unchanged: a blocking console loop has no event loop to
  starve, and its `out.write(chunk)` + flush already streams incrementally.

## Evidence

Probe (`/tmp/fworker-wq4522/probe_blocking.py`, reproduced as a committed test): real
`LimpetChatApp` under Textual's `run_test()` pilot, backend = 4 chunks × `time.sleep(0.4)`
(1.6s reply), with `_refresh_tools` — the method `on_mount` wires to
`set_interval(1.0, ...)` — subclassed to record fire times.

| | press blocked | timer fires mid-stream | timer fires at all | reply |
|---|---|---|---|---|
| pre-fix (`89782767`) | **1.703s** | **0** (1.30s window, 1.0s period) | 1, at t0+1.613s | `abcd` |
| post-fix | **0.110s** | 1, at t0+0.970s | 1 | `abcd` |

"mid-stream" counts fires before `t0 + total − 0.4s`, a window longer than the timer's
own period, so a timer that merely catches up once the blocking loop releases (pre-fix:
one late fire at t0+1.613s, 0.6s after its deadline) does not count. Pre-fix: zero.

The probe input comes from outside the population the original code was designed around
(wq#2630): the shipped suite only ever drove the app with `backend=None` or an instant
generator, both of which pass either way. Verified falsifiable by running the committed
probe against the pre-fix module extracted from `git show 89782767:limpet/agent_tui.py`
— it fails there on the press assertion and on the mid-stream timer assertion.

Tests: `python3 -m pytest limpet/test_agent_tui.py -q` → **19 passed** (17 pre-existing +
2 new), run twice including the default random order. New tests:
`test_textual_streaming_keeps_event_loop_live` and
`test_textual_streaming_renders_backend_failure_and_recovers`
(`limpet/test_agent_tui.py:270`, `:324`).

## What this does not fix

- The backend contract stays synchronous (`Callable[[str], Iterable[str]]`). A thread
  worker is the right shape for it, and the limpet-session client will be a blocking
  HTTP/SSE read; if that client ends up natively async, the worker can become a plain
  `@work` coroutine without touching the handler.
- Streaming is still one in-flight turn per session, by construction (above). Multi-turn
  concurrency needs turn ids in the §6 event protocol first — that belongs to the
  limpet-session ticket, not here.
- Spec §4.4's workspace header (target_id / identity / git-dirty are computed but never
  rendered) is still open — wq#4521 noted it, and it is out of this ticket's scope.

## Operator note (`python3 -I`)

The first probe run failed with `module agent_tui has no attribute LimpetChatApp`:
`python3 -I` implies `-s`, textual lives in the user site, so the module's optional-import
`try/except ImportError` silently disabled the whole Textual block. That looks like a code
bug and is not one. Logged as ISSUE-LOG row 1524.
