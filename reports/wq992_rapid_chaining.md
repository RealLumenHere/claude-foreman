# wq#992 — rapid-chain tickets without the 20-min slot-idle gap

Shipped 2026-08-27. PR #157 (squash `8fa53bc`), merged and fast-forwarded onto the live
primary tree.

## What Frank asked for vs what shipped

1. **Rapid chaining** — on a worker's clean exit, dispatch the next ticket immediately,
   not at the 20-min timer. → **B**: the worker fires one detached dispatch pass on a
   slot-freeing exit. Verified live: kick -> pass in 8s.
2. **Clean exit on completion** — the harness must stop the agent once done, before
   auto-continue burns tokens. → **C**: run_child watches ticket status and stops the
   session shortly after it reads done, routed to verified-success (never TURNS).
3. **Pipeline / hot queue** — zero idle gap, the 3-concurrent ceiling the only throttle.
   → delivered by **B's mechanism**, not a separate pre-staged queue. Event-driven refill
   means the instant a slot frees a pass runs and launches the next candidate; the ceiling
   is the only throttle. Same zero-gap outcome as pre-staging, simpler, and no risk of a
   stale hot-queue launching a ticket whose priority or deps changed after staging.
   Candidate selection is sub-second, so pre-staging would save nothing over running a
   pass on exit.
4. **tickets/hour metric (bar ~7/hr)** — → `ops/wq992-throughput/`. Baseline at ship
   0.92/hr, peak clock-hour 7 done. Sibling to the R4 ledger, not bolted onto it.

Plus **A** (not in the ask, but required): wq#992 itself would not route. Its title
matched an over-broad `\bchain\b` signal on the long-horizon-campaign archetype, hitting
DECOMPOSE then route_unassigned. Added corroboration to that signal (the gate wq#778
already uses). wq#992 now routes standard-engineering -> sonnet-5@high.

## The corrected diagnosis

Frank's framing said the dispatcher managed ~10 tickets in 4 days and was idle between
tickets. Live state disproved the "idle" and "10/4d" parts: the dispatcher spawns 3
workers every ~20 min, ceiling-throttled at 3 cloud slots, ~59-65 candidates queued; the
build lane alone is 289 done. The real loss is **slot-refill latency** — a freed cloud
slot sits idle up to ~20 min until the next timer pass refills it. That is the gap versus
Frank's manual finish -> relaunch. route_unassigned was a router signal misfire, not a
build-lane fault (the lane was healthy).

## Mechanism notes

- B kicks after a short settle so the worker's own systemd unit deactivates first —
  otherwise concurrency counting still sees it as running and masks the slot it freed.
  The pass is slock-serialized, ceiling-bounded and idempotent, so a burst of near
  simultaneous completions coalesces and cannot over-spawn. Only EXIT_OK and EXIT_BLOCKED
  kick (a run that reached a verdict and freed a slot); pre-claim deferrals and LIMIT do
  not. Fail-open; the 20-min timer remains the backstop for hard deaths that fire no kick.
- C forces SUCCESS on killed_reason=ticket_done, and the existing C10 artifact gate then
  re-confirms status==done, so a racy flip-back to open self-corrects to a resume rather
  than a force-close on a stale read.

## Testing

- New: router gate 6, refill guards/shape/fail-open 6, done-watch integration 2,
  throughput 5. All pass.
- Whole harness suite run per-file on the branch and on main, diffed by name: 13
  pre-existing failures (subprocess/shim/CLI-drift in this container) identical on both
  trees, none introduced.
- Live: kick -> dispatch pass in 8s; wq#992 routes; throughput baseline emitted.

## Follow-ups filed

- Pre-existing test debt: `orchestrator/tests/test_router.py::test_assign_and_verify_pass_fire_on_scratch_wq`
  (wq add output-format drift) and the 13 harness env/shim failures. Separate ticket.

## Observation for Frank (not acted on)

A dispatch pass halted spawning with `psi_full_avg60=0.02%>=0.0%` — the PSI-full spawn
threshold appears to be 0.0%, which halts on almost any memory pressure and may itself be
throttling throughput independently of the refill work. Worth a look; out of scope here.
