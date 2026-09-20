# DISPATCH_FIX_RESEARCH — how to fix it durably, and what to borrow (Phase 2 of 3)

orch-dispatch-dissect-62c4e5 · 2026-09-20 · read-only. Input: `DISPATCH_DISSECTION.md` flaws F1-F9.
Output feeds `DISPATCH_REBUILD_SPEC.md`.

## 1. The RCA's open questions, answered against the dissection

### 1.1 Should local and cloud be separate loops?

They already are, in the only sense that mattered: one pass, two partitions, local first, pool-scoped
stops (wq#2088, verified in DISSECTION 3.2). A second daemon is still the wrong move for the reasons
the RCA gave (shared `state.json`, doubled scans, interleaved logs). The lock is not a bottleneck:
22 s passes, 1 collision in 164 passes.

What DOES need to be separate is everything that is still pool-blind:

| shared today | should be |
|--------------|-----------|
| one frame-gap watchdog, 240 s | per-pool: cloud 240 s; local = first-frame budget sized from measured prompt-eval rate, then a normal gap |
| one `MAX_ATTEMPTS` and one lifetime runaway counter | failure CAUSE decides who is charged: ticket, rung, or harness |
| one circuit breaker window | one window per pool; a local trip pauses the local partition only, and never writes the global DISARM |
| one `local_ceiling` integer | per-backend slots (hathor 2, nephthys N, isis N), discovered not guessed |
| one PSI tight cap across the sum of both partitions | keep: PSI is about Lumen's RAM, and every worker costs a CLI process on Lumen |

### 1.2 Should the gates be restructured? Real safety vs accidental blockers

| gate | class | disposition |
|------|-------|-------------|
| kill switch, PSI deny | safety | keep |
| governor / burn rate / Frank reserve / cloud_ceiling | safety (budget) | keep, cloud only, unchanged |
| capability deny on secrets: `.secrets`, `.ssh`, `.claude`, `.config`, `*.env`, `*.token`, `*.key`, `*.pem`, `id_*`, `queue.db*`, fresh secret-sweep hit | safety (secrets) | keep fail-closed; ADD basename denies for home-root secrets (`.fleet_pw`, `.git-credentials*`) so they stop depending on the `/home/lumen` parent accident |
| capability: dir not in `allow.read_dirs` | over-broad | downgrade to ADVISORY. Dispatch without the grant; the worker's permission layer refuses the read if it really tries; record `needs_advisory` on the ticket. The grant remains opt-in via `needs:dir(...)` + policy PR. |
| capability: cited path missing, worktree path mention, home-root non-secret file | accidental | remove as blockers (log only) |
| capability: v2 verb word | heuristic | block only when the verb is in an imperative position in the ticket's acceptance/steps section or in a fenced command; a bare prose mention becomes advisory. Tickets that truly need privileged verbs are Frank-lane work and should be laned there. |
| frank_gated, nodispatch, held, deps, fire approval, already_running | correct | keep |
| lane_cap | correct | keep |
| lane_unmapped, repo_mismatch, route_unassigned | correct mechanism, data defect | keep the refusal; add auto-remediation (section 1.4) |
| runaway_stuck | wrong accounting | count only ticket-charged attempts; decay; reset on re-pin / re-route |
| spawn_loop | wrong exit | after the second trip, move the ticket to `blocked` with a cause, do not roll the window forever |
| harness_defect_hold | correct | keep; make it structural by construction (section 1.3) |

### 1.3 The metric: stop enumerating reasons

`dispatchable := len(ready)` per pool, full stop. A ticket is dispatchable iff it passed every gate and
resolved a model. Every other outcome is, by definition, not a dispatcher stall. This deletes
`STRUCTURAL_SKIP_REASONS`, `count_structural_skips`, `dispatchable_unpooled`, and the whole bug class
(4 recurrences: wq#1410, 09-08, wq#2047, and harness_defect_hold today). The watchdog rule becomes
"ready_pool > 0 and spawned_pool == 0 and no slots-full / partition-stop explanation".

### 1.4 Should lane_cap / lane_unmapped be redesigned so the pool is not starved?

They are not what starves it. lane_cap is 16 pilot tickets behind a deliberate 3/day budget.
lane_unmapped is 12 tickets, a data problem: `auto` is a catch-all lane with no repo, `simpill` was
enabled in config.json without a `LANE_REPO` row. The durable fix is at ticket-creation time plus one
janitor, not in the pass:
- `wq add/edit` refuses (or warns loudly on) a lane that is enabled for dispatch but has no repo,
  unless the ticket carries `needs:repo(...)`.
- a `dispatch_janitor` pass (section 3, borrowed from Oban's Lifeline) owns every GATED_TERMINAL
  ticket: it files ONE digest row per cause per day instead of one ntfy per ticket, and applies the
  mechanical remediations (re-lane from a title repo marker, clear a stale failure.json hold, release
  rung-fault blocks).

## 2. The fix for the actual root causes (F1-F5)

### 2.1 Make the Macs able to finish one attempt (F1, F2)

Measured: CPU-only prompt eval. Cold TTFT on ~2k tokens: hathor llama3.1:8b 142 s, nephthys
qwen3:14b 265 s. Served context 4096 on both. Therefore, in order:

1. Serve the context the manifest promises. Ollama picks `num_ctx` from `OLLAMA_CONTEXT_LENGTH`
   (server env) or a Modelfile `PARAMETER num_ctx`; the OpenAI-compatible `/v1` route cannot pass it
   per request. Either set the env on each Mac's ollama unit, or create derived models
   (`ollama create llama3.1-8b-32k` with `PARAMETER num_ctx 32768`) and point LiteLLM at those.
   RAM check (KV cache at f16): llama3.1:8b ~0.125 MB/token, so 32k = ~4 GB per slot; qwen3:14b
   ~0.16 MB/token, so 32k = ~5 GB per slot. 64 GB boxes hold 2 slots each comfortably. Verify with
   `/api/ps` `context_length` after the change, not by reading config.
2. Measure prompt-eval tokens/s per box with `ttft.py` at 2k / 8k / 16k WARM (model resident).
   That number, not a guess, sets the local first-frame budget:
   `first_frame_s = prompt_tokens / eval_tps * 1.5 + model_load_s`.
3. The honest constraint. Measured this session (approximate token counts, 4096-ctx truncation in
   effect): ~14-15 tok/s prompt eval on both boxes. A 16k-token fworker prompt is then an ~18 min
   first frame; a 4k prompt ~4.5 min. Saturation is then trivially achievable (the boxes will be busy) but throughput at
   full prompt size is on the order of 1-3 tickets/hour/box (estimate, to be replaced by step 0's warm numbers). The lever that actually moves throughput is prompt size:
   - a SLIM local prompt profile (no full tool catalogue, no instincts pack, no fleet-tools
     registry; archetype `mechanical-edit` / `probe-build` only need read/edit/bash), target <= 4k
     tokens;
   - KV prefix caching: keep the static prefix byte-identical across tickets and first in the prompt
     so ollama/llama.cpp reuse the cached prefix; `OLLAMA_KEEP_ALIVE` long enough that the model and
     its cache stay resident between tickets (hathor 30 m is fine; nephthys 5 m is too short);
   - one slot per worker, never more workers than slots (2.3), so nobody's eval is time-sliced.
4. Timeouts, per pool, in `fworker.py`: `FIRST_FRAME_TIMEOUT_S` (local: from step 2; cloud: 240)
   separate from `INACTIVITY_TIMEOUT_S` (gap between frames AFTER the first). Today one constant
   (`:285`) does both jobs.

### 2.2 Charge failures to the right party (F3)

Borrowed directly from Oban (`{:snooze, s}` does not consume an attempt as of v2.24; `{:cancel, r}`
is terminal; errors consume an attempt with backoff) and Temporal (non-retryable error types vs
retryable with a policy; schedule-to-start timeout is a QUEUE problem, start-to-close is a TASK
problem). Mapping:

| fworker outcome | charged to | effect |
|-----------------|-----------|--------|
| HANG with `turns == 0` on a local model; LiteLLM 5xx/timeout/cooldown; backend unreachable; ctx-overflow error | the RUNG | attempt NOT charged to the ticket; ticket released to `open`; backend marked unhealthy with cooldown (2.3); after 2 rung faults on the same backend the ticket is excluded from THAT backend only |
| LAUNCH_FAILURE, SpawnRefused, worktree errors | the HARNESS | as today (uncharged, harness_defect hold) |
| LIMIT | the BUDGET | as today |
| TURNS, FALSE_COMPLETE, PERMISSION_LOOP, CRASH with turns > 0, HANG with turns > 0 | the TICKET | charged; existing MAX_ATTEMPTS and runaway logic, but runaway counts ticket-charged attempts inside a 7-day window and resets when model or archetype changes |

### 2.3 Backpressure from real capacity (F5) and routing to the Macs (F4)

LiteLLM already sits between every worker and every Mac, and its router already has the three
mechanisms missing from the dispatcher: a model GROUP spanning several deployments,
`routing_strategy: least-busy`, per-deployment `max_parallel_requests`, and failure cooldowns
(`allowed_fails`, `cooldown_time`), plus `enable_pre_call_checks` to refuse a prompt larger than a
deployment's context window instead of letting the backend truncate it.

Design:
- one LiteLLM model group `local-worker` with a deployment per healthy backend, each with
  `max_parallel_requests` = that backend's real slot count and `model_info.max_input_tokens` = its
  SERVED context;
- the dispatcher's local ceiling = sum of slots of backends that answered a health probe in this
  pass (`/api/ps` or `/health`, 2 s timeout, cached per pass). 3 boxes x 2 slots = 6, not 12;
- `models.yaml` routing rows gain a local primary for the archetypes the Macs are trusted with
  (`fworker.local_allowed_archetypes`: mechanical-edit, probe-build, and standard-engineering on the
  large-ctx models), with the existing fails-walk to sonnet as the safety net and the existing
  `verify_pass.local_author_always` gate unchanged;
- `router.select` skips a rung whose model `status` is not `LIVE*` or whose backend is in cooldown.

### 2.4 Gated tickets become a queue state, requeued by events (F8)

kube-scheduler's scheduling queue is the reference design: `activeQ` (try now), `backoffQ` (retry
after backoff), `unschedulablePods` (parked until a cluster EVENT a plugin registered for says the
pod might now fit: `QueueingHint`), with a periodic flush as the safety net (5 min default
`DefaultPodMaxInUnschedulablePodsDuration`). The dispatcher today is a scheduler with only an activeQ
that re-runs every filter on every pod every cycle.

Lumen-sized version, no new daemon:
- a `dispatch_gate` table in queue.db: `(ticket_id, reason, detail, fingerprint, first_seen,
  last_seen, recheck_after)`. `fingerprint` = hash of the inputs that gate read (ticket
  `updated`-relevant columns, policy file mtime, LANE_REPO hash, dep statuses).
- a gated ticket is re-evaluated only when its fingerprint inputs change or `recheck_after` passes
  (deps: on dep done; lane_cap: next UTC midnight; held: hold_expires; terminal: 6 h safety flush).
- the pass evaluates `open - parked`. At today's numbers that is ~0-10 tickets instead of 120, the
  log shrinks ~95 %, and `wq list` can show WHY a ticket is not running (`wq show` gains a
  `dispatch:` line), which is the thing Frank actually asks.

## 3. GitHub scrape: what others do better

Method: `/home/lumen/bin/fleet-search github` (tokens checked OK by `github-token-check`: 3 live)
with the six mission search terms plus three of mine; raw results in
`/home/lumen/scratch/dissect/gh_scrape.txt` and `gh2.txt`. The keyword searches return mostly small
hobby repos (listed in the raw files; none adopted). The designs worth copying are in the mature
projects below. "Verified" = repo resolved through the GitHub API via fleet-search with stars and a
push date inside the last 30 days. File references are to upstream paths I could confirm from
source or official docs in this session; where I could not open a file I say so.

| project | verified | what it does better than Lumen's dispatcher | adopt |
|---------|----------|---------------------------------------------|-------|
| **kubernetes/kubernetes** scheduler (`pkg/scheduler/backend/queue/scheduling_queue.go`; docs: Scheduling Framework, "QueueingHint" blog 2024-12-12) | core k8s, current | three-queue model; Filter plugins return a STATUS with the plugin name, and a rejected pod is parked and requeued only on events that plugin subscribed to; periodic flush as backstop; `PreEnqueue` plugins keep gated pods out of the active queue entirely | section 2.4; each gate becomes a small object with `check()`, `reason`, `terminal`, `recheck_on` so ordering and metrics are data, not 170 lines of if/return |
| **oban-bg/oban** (3974 stars, pushed 2026-09-20; `lib/oban/plugins/lifeline.ex`) | yes | per-queue concurrency limits as config; job states `available / scheduled / executing / retryable / completed / discarded / cancelled` are REAL columns; `snooze` does not burn an attempt (v2.24.0 release notes); Lifeline rescues orphans on a timer and discards exhausted ones; pruner | 2.2 charging model; janitor = Lifeline; "retryable with backoff" instead of immediate re-spawn |
| **riverqueue/river** (5693 stars, pushed 2026-09-20) | yes (repo level; files not opened) | Postgres `SKIP LOCKED` claim, per-queue `MaxWorkers`, leadership-elected maintenance services (rescuer, scheduler, reindexer) separate from the work loop | confirms the janitor-as-separate-service split; nothing further |
| **temporalio/temporal** (23189 stars, pushed 2026-09-20) | yes (repo level) | workers PULL from a named task queue per worker pool, so capacity is self-describing: a worker with 2 slots polls 2 tasks; four distinct timeouts (schedule-to-start, start-to-close, schedule-to-close, heartbeat); non-retryable error types | 2.1 step 4 (separate first-frame vs gap timeout), 2.2 cause typing. A pull model is the ideal end state for the Macs (a tiny agent per Mac that asks for work when a slot frees); out of scope for this rebuild, noted as the follow-on |
| **celery/celery** (28902 stars, pushed 2026-09-20) | yes (repo level) | dedicated queues per worker pool (`-Q`), `worker_prefetch_multiplier=1` + `acks_late` for long tasks so a slow worker never hoards, per-task `rate_limit`, `autoretry_for` exception lists with backoff | the prefetch=1 rule is exactly "never more workers than slots" |
| **BerriAI/litellm** router (`litellm/router.py`, `litellm/router_strategy/`; docs: routing, `router_settings`) | already deployed on Lumen | model groups, `least-busy`, `max_parallel_requests`, `allowed_fails` + `cooldown_time`, `enable_pre_call_checks`, per-exception `retry_policy` | section 2.3, config only |
| **ClipboardHealth/groundcrew** (66 stars, pushed 2026-09-19; README read via `fleet-search read`) | yes | opt-in dispatch: a task without an `agent-*` label is IGNORED, not skipped-and-logged; `agent-any` routes to the agent with the most session headroom after skipping agents over their session or weekly paced budget; blocked-by relations skip until blockers are done; `crew doctor` preflight; one worktree per task | the opt-in idea = HUMAN_LANES never enter the candidate list; `agent-any` = our headroom-aware rung choice (already have burn_rate; extend the idea to local slot headroom) |
| alamops/agetor (71), sonpiaz/evox (104), vnovick/itervox (40), tumf/conflux (26) | exist, recently pushed; NOT examined at file level | kanban-style agent orchestrators | nothing claimed; listed so the implementer does not re-search |

Not found / search returned empty (GitHub search rate limiting mid-run): hatchet-dev/hatchet,
procrastinate, dramatiq, vibe-kanban, claude-squad. Not evaluated; no claims made.

## 4. Synthesis: fix + improvements, with expected gains

| # | change | fixes | expected effect (from today's numbers) |
|---|--------|-------|----------------------------------------|
| S1 | Mac serving: real context + keep-alive; LiteLLM `local-worker` group with per-backend `max_parallel_requests`, least-busy, cooldown, pre-call ctx check | F2, F5 | prompts stop being truncated; no request ever queues behind another on a CPU box |
| S2 | fworker: per-pool first-frame timeout; slim local prompt profile; static-prefix-first for KV reuse | F1 | local success rate from 0/26 to non-zero. This is the single change that turns the Macs on. |
| S3 | cause-typed failure charging; rung-fault release; runaway = ticket-charged, windowed, reset on re-route | F3 | the 22 `UNKNOWN: HANG` blocks and the HANG share of 14 runaway_stuck become eligible again (one-time recovery of ~25-30 tickets) and stop recurring |
| S4 | routing rows + status/cooldown-aware `router.select`; dispatcher local ceiling = live slots | F4, F5 | local supply stops depending on hand pins: of 120 open, the 12 standard-engineering + 2 mechanical-edit + 2 probe-build typed tickets, plus whatever share of the 76 untyped classify into those archetypes, become local-eligible |
| S5 | capability lint: secrets stay hard; everything else advisory; verb heuristic narrowed | F6 | ~32 of 44 capability-held tickets released (27 % of the queue); ntfy volume from 55/day to the real-secret handful |
| S6 | `dispatchable = len(ready)`; gate objects; `dispatch_gate` parking table with fingerprints + recheck; janitor with daily digest and mechanical remediations | F7, F8, F9 | false stall pages end by construction; per-pass work from 120 tickets to ~0-10; log volume down ~95 %; spawn-loop tickets leave the pool after the second trip |
| S7 | per-pool circuit breaker; merge the two watchdogs into dispatch_health/recover; fix ladder timeouts; commit the timer drop-in; install or delete the burnrate timer | ops debt | one alarm path, no pool can disarm the other |

What "saturated" will honestly look like after S1-S4: 3 boxes x 2 slots = 6 concurrent local workers,
each box at load ~24 (all threads in prompt eval / decode) whenever ungated local-eligible work
exists, with throughput bounded by prompt size. If warm eval rates come back under ~20 tok/s, the
slim prompt profile (S2) is not optional: it is the difference between ~3 and ~12+ tickets/hour
across the three boxes.
