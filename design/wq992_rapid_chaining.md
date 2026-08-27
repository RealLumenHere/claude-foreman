# wq#992 — dispatcher chains tickets without the 20-min slot-idle gap

## Problem, as measured (not as reported)

Frank's report: manual ~40 tickets / 6h (~7/hr); dispatcher ~10 / 4 days. Diagnosed
causes: (1) 20-min idle timer, (2) auto-continue burning tokens, (3) wq#992 skipping on
`route_unassigned` on the build lane.

Verified against live state before building (instinct: NARRATIVE-KILL / RUN-THE-FAILING-THING):

- The dispatcher is **not idle**. It spawns 3 workers every ~20 min, `candidates=59`,
  ceiling-throttled at `cloud_ceiling=3`. Build lane has **289 done / 5 open / 36 blocked**,
  not 10. "10 in 4 days" was a broken-period figure; current cadence is healthy but capped.
- The real loss is **slot-refill latency**: when an fworker exits mid-window it frees a
  cloud slot, and nothing refills it until the next 20-min timer pass. With 3 slots and
  mid-window completions that is up to ~20 min of dead slot-time per completion — the exact
  gap between "finish → immediately relaunch" (Frank's manual loop) and "finish → wait for
  the timer".
- (3) is **not** a build-lane fault. The build lane is enabled and healthy. wq#992 is
  unroutable because its title ("dispatcher must **chain** tickets…") matches the
  `\bchain\b` signal on archetype `long-horizon-campaign`, whose primary rung is
  `DECOMPOSE`. `candidate_model()` returns None for a DECOMPOSE rung → stage-8
  `route_unassigned`. One over-broad regex, not the lane.

## Class analysis

**A — `route_unassigned` (class = generic bare word forcing an expensive archetype).**
`long-horizon-campaign` → DECOMPOSE is the most consequential routing the classifier can
emit (splits one ticket into many). Its signal list is otherwise strongly campaign-specific
(`campaign`, `multi-session`, `checkpoint`, `uplift`, `pivot`); `\bchain\b` is the outlier —
it appears in ordinary prose ("chain tickets", "routing chain", "tool-chain", "dependency
chain"). This is exactly the class wq#778 already fixed for `research-synthesis` /
`rca-investigation` (bare `research`/`report`/`audit`/`why` routing opus off one word). The
existing corroboration gate (`_qualifies` / `_WEAK_SIGNALS`) just was never extended to the
DECOMPOSE-primary archetype. Fix the class by extending that same gate, not by deleting the
signal.

**B — slot-refill latency (class = event that frees a resource has no event-driven
consumer; only a periodic sweep).** A freed cloud slot is an event; the only thing that
consumes it is the 20-min timer. Fix: make worker completion an *event* that triggers a
dispatch pass, and demote the timer to the stall-backstop it already documents itself as.

**C — post-completion turn burn (class = terminal work-state has no terminal signal to the
supervisor).** The streaming supervisor already kills on: frame-gap, wall-clock,
turn-backstop, `result`-frame-then-grace (§C9 post_result_stuck), and repetition
(loop_tracker). The one terminal state with **no** watchdog is: the agent ran `wq done`
(ticket is genuinely finished) but keeps taking agentic turns **without** emitting a
`result` frame — so post_result_grace never arms and it burns to the turn-backstop, which
misclassifies as TURNS and triggers a wasteful fresh-worktree restart **on an already-done
ticket**. Fix: mirror post_result_stuck, keyed on ticket-status→done.

## Chosen approach

### A — router corroboration gate (orchestrator/router.py)
- Generalize the wq#778 gate: `_CORROBORATION_ARCHETYPES = _OPUS_PRIMARY_ARCHETYPES ∪
  {"long-horizon-campaign"}`; `_qualifies` gates any archetype in that set.
- Add `("long-horizon-campaign", r"\bchain\b")` to `_WEAK_SIGNALS`.
- Result: `\bchain\b` alone no longer wins long-horizon-campaign (falls through to
  standard-engineering); `chain` + a real campaign word still classifies as campaign; a
  bare `campaign`/`multi-session`/`uplift`/`pivot`/`checkpoint` still classifies alone
  (they are not weak signals). Nothing else moves.

### B — immediate refill on worker exit (harness/fworker.py)
- On **every** terminal exit of fworker (success, block, limit-release — all free a cloud
  slot), fire one dispatch pass. Implemented in `main()` after `cmd_run` returns, so it
  covers all paths in one place.
- **Race:** fworker runs inside its own `fworker-wq<ID>` lumen-run unit;
  `concurrency.list_running_units` counts units whose `lumen-run list` line says "running".
  A synchronous trigger could run the pass while fworker's own unit is still deactivating →
  slot counted as full → no refill. Closed deterministically with a short **detached**
  kick: `systemd-run … sh -c 'sleep <settle>; systemctl start --no-block
  lumen-foreman-dispatch.service'`. fworker exits immediately (slot frees); the pass runs a
  few seconds later against a provably-free slot.
- **Safety:** honors the DISPATCH_DISARM kill switch (no kick when disarmed); the pass
  itself is slock-serialized (concurrent completions coalesce), ceiling-bounded, and
  idempotent, so a burst of kicks can never over-spawn. `--no-refill` env/flag for tests.
- The 20-min timer stays as the stall/crash backstop (a hard fworker/​supervisor death
  fires no kick — the timer and reaper.py catch that, unchanged).

### C — done-watch in the streaming supervisor (harness/fworker.py run_child)
- Add `done_seen_at`, mirroring `result_seen_at`. Poll `wq_show(ticket).status` on a
  throttled cadence (`DONE_POLL_INTERVAL_S`, default 15s) inside the loop. First time it
  reads `done`, stamp `done_seen_at`.
- After `POST_DONE_GRACE_S` (default 60s — comfortably longer than a normal trailing
  episode-commit / final message, far shorter than the turn-backstop's minutes) with the
  process still alive, SIGTERM with `killed_reason="ticket_done"`.
- Classification: `ticket_done` is a **success** signal, not a failure. It routes through
  the existing `check_artifacts_done` path (status==done ⇒ True) → close-if-needed +
  verify-pass → EXIT_OK. It must never reach handle_failure/TURNS.
- Grace is generous on purpose: the good case (agent runs `wq done` last, emits its result,
  exits) never triggers it — the child is already gone. It only bites the lingering case.

### Metric (R4 intervention ledger)
- Add tickets/hour throughput to ops/intervention_ledger.py telemetry, derived from wq
  `done_at` timestamps over a trailing window. Bar to beat = Frank's manual ~7/hr.

## Test-to-failure plan
- **A:** unit-test classify_archetype: bare "chain tickets" title ⇒ NOT long-horizon-campaign;
  "multi-session chain campaign" ⇒ long-horizon-campaign; bare "campaign" ⇒ still campaign;
  the four wq#778 opus cases unchanged. Then live `router.assign(992)` ⇒ real rung.
- **B:** live — dispatch a trivial ticket, let the worker complete, assert a new spawn
  appears within 60s of exit (log timestamps). Test the disarm path (DISPATCH_DISARM ⇒ no
  kick). Test the coalesce path (kick while a pass holds the slock ⇒ second pass halts on
  lock_busy, no double-spawn).
- **C:** synthetic stream harness — feed frames that run `wq done` then keep emitting
  tool_use frames with no `result`; assert the child is SIGTERM'd within
  POST_DONE_GRACE_S+poll and classified EXIT_OK, not TURNS. Assert the good case (result
  frame right after done) is untouched. Run each until a full clean cycle.

## Rollback
- A: revert the router.py hunk (2 lines + 1 set). B/C: `.bak` of fworker.py; revert hunks.
  All three are additive and independently revertable. DISPATCH_DISARM instantly neutralizes
  B without a code change. Nothing changes the dispatcher's decision pipeline, lane config,
  or the concurrency ceiling.
