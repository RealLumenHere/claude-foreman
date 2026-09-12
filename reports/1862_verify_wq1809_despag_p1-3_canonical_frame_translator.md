# wq#1862 — Adversarial verify-pass of wq#1809 (despag P1-3: canonical-frame -> UHP response.* translator, gapless SSE, keep-alive, cancelled terminal)

**Verdict: CONFIRM** — `9f4ff27` (HEAD of `main` at review time, `/home/lumen/foreman`,
already merged) reproduces its own claimed 40/40 Core conformance result and 121/121
pytest, and holds up under adversarial probing beyond what the shipped test suite
exercises: cancel, wall-clock timeout, and frame-gap hang, all fired mid-stream against
a live SSE connection, all three produced the correct single terminal event with the
correct status and correct partial-output retention.

## What was shipped

`despag/uhp/translate.py` (`RespTranslator`/`blocks_from_canonical`, landed via the
`0cf66ab` merge alongside `despag/runner/turn.py`'s `on_frame` live-SSE bridge and
`despag/runner/canonical.py`), `despag/app.py` (`_build_translator`, `_finish_events`,
`_response_view`, `_sse_gen`, `_stream_response`, wiring `stream:true` into
`POST /v1/responses`), `despag/tests/test_uhp.py` (3 new SSE tests), plus committed
conformance reports (`despag/reports/p1-3_core_{litellm,claudemax}.json`) and an
`evidence.json` AC1 citation.

## Method — ran it, not just read it

- Read `despag/uhp/translate.py` (249 lines) and `despag/app.py` (657 lines) in full,
  not just the diff — the true diff base is `0cf66ab` (a merge that authored
  `translate.py`/`on_frame`/`canonical.py` net-new, not present in either parent — a
  process smell worth flagging but not a correctness defect), not `6cdd9da^` as the
  branch name implies.
- `cd /home/lumen/worktrees/wq1862-a1 && python3 -m pytest despag/tests/ -q` →
  **121 passed**, matches the shipped claim.
- Read the committed `despag/reports/p1-3_core_litellm.json`: `conformant: true`,
  `40/40 pass, 0 fail/skip/error`, `generated_at: 2026-09-12T15:55:10Z` (same day as
  the merge, not stale). Spot-checked the streaming/cancellation checks by id: S-01..
  S-09 (gapless sequencing, `response.created` first, single terminal, terminal
  carries full response, stream/non-stream parity, progressive-not-buffered) and
  C-02/C-03 (cancel idempotent-on-terminal, cancel-while-running) all `pass`.
- Three adversarial probes not present in the shipped suite, run live against
  `fastapi.testclient.TestClient(despag.app.app)` with `fake_backend.py` standing in
  for `claude -p` (scripts discarded after the run, not part of this report's diff):
  1. **Cancel mid-SSE-stream** — POST `stream:true` with `--mode steady` (an endless
     ticker) on a background thread, wait for the response id to appear in
     `app_module.RESPONSES`, let a few `output_text.delta`s land, then
     `POST /v1/responses/{id}/cancel` from the main thread while the stream is still
     open. Result: exactly one terminal (`response.failed`), `sequence_number` gapless
     0..N, `response.status == "cancelled"`, and the partial text
     (`"tick 0tick 1tick 2tick 3tick 4tick 5"`) retained as a completed `message`
     output item — matches the ticket's AC verbatim.
  2. **Wall-clock timeout mid-stream** — `timeout_seconds: 1` against the same endless
     ticker. Result: one terminal `response.incomplete`,
     `incomplete_details.reason == "timeout"`, partial output retained.
  3. **Frame-gap ("hang") mid-stream** — `DESPAG_FRAME_GAP_SECONDS=1`, `--mode silent`
     (one init frame then nothing). Result: a non-fatal `error` event
     (`code: harness_error`, `"no frame for 1s (frame-gap watchdog)"`) immediately
     followed by exactly one terminal `response.failed` — matches "non-fatal error
     event always followed by a terminal" and the `hang -> failed` (not `incomplete`)
     mapping in `_STATUS_MAP`, since a hang is not a budget stop.

  All three exercise the exact race the code's own comments call out as load-bearing
  (`_finish_events`' single-close-per-response cache, the reader thread's
  `on_frame`/`done.set()` ordering) and none broke.

## Adversarial angles tried, nothing found

- `_finish_events`/`_response_view` caching (`rec["_final"]`): a GET racing the SSE
  path's own terminal, or two callers hitting `_finish_events` concurrently, could in
  principle mint two independent `RespTranslator` closes with different `uuid4()` item
  ids. Traced it: both functions are fully synchronous (no `await` between the
  check-then-set), so under the single-threaded asyncio event loop the check-then-act
  is atomic — no interleaving is possible. Confirmed by `test_stream_idempotency_key_
  replays_settled_output` and by inspection.
- `turn.py`'s `on_frame` bridge: appends to `self.frames` under `_frames_lock`, then
  calls `on_frame(ev)` outside the lock but still on the single reader thread, so
  event order is preserved and `call_soon_threadsafe(_feed)` for the terminal frame is
  provably enqueued on the loop before the `done.set()`-triggered continuation of
  `_finish()` — the translator always sees every frame before it is closed.
  Reasoned through via `threading.Event`/`call_soon_threadsafe` happens-before
  ordering; the three live probes above are the empirical check of the same claim.
- `metadata.ignored_fields`/reserved-fields handling (T-08/T-09): `CreateResponseRequest`
  is `ConfigDict(extra="allow")`, so `tools`/`include` don't 400 before the handler can
  record them; `ignored_fields` is computed from the raw request `body` dict (key
  presence, not truthiness), so it never reports a field the client didn't send.
  Confirmed by reading, covered by the shipped conformance report's T-08/T-09 pass.
  `usage` stays `None` (never a fabricated zero) unless a `result` canonical frame
  actually carries one — checked `RespTranslator.__init__`/`_handle("result", ...)`.
- SSE transport headers/keep-alive: `Cache-Control: no-cache`, `X-Accel-Buffering: no`
  set on the `StreamingResponse`; no `GZipMiddleware` anywhere in the app (grepped), so
  there is nothing to double-compress the stream; keep-alive comment fires on exactly
  the documented 20s `asyncio.wait_for` timeout, never as a polling interval for real
  events (`queue.get()` is awaited directly).
- Event ordering inside `_close_cur`/`_handle` for each block kind (`output_item.added`
  before any event about it, `output_item.done` after all of them; `output_text.delta`
  -> `output_text.done` -> `content_part.done` for messages;
  `function_call_arguments.delta` -> `.done` for tool calls) matches the ticket's
  described vocabulary and ordering.

## Process note, not a defect

`despag/uhp/translate.py`, `despag/runner/turn.py`'s `on_frame`, and
`despag/runner/canonical.py` were authored net-new inside the `0cf66ab` merge commit
(neither parent has them), not inside a normal feature commit — despite the merge
message framing this as reconciling `wq#1806`. Worth a process note for whoever reviews
merge hygiene on this lane, but the content itself is correct and covered by
`test_translate.py`/`test_runner.py`.

## Bottom line

CONFIRM. Read the translator and the app wiring in full, reproduced the claimed
pytest and conformance counts live, and threw three targeted adversarial scenarios
(cancel/timeout/hang, all mid-stream) at the SSE path that the shipped suite does not
cover — all three landed exactly on the ticket's acceptance criteria. No re-
implementation performed per the verify-pass protocol.
