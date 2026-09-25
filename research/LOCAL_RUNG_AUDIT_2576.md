# Local LLM rung audit (wq#2576)

Snapshot 2026-09-25T04:18Z, 30-day window. Reproduce with
`python3 research/local_rung_audit.py [--days 30] [--json]` (read-only: queue.db +
`/home/lumen/foreman/_runs/wq*/attempt-N.json`).

## Verdict: NARROW, to zero agentic fworker work

No local model has ever closed a fworker ticket. Across all of `_runs`, 0 of 166 local
fworker sessions were classified SUCCESS. Of the 41 local-model tickets marked `done` in the
last 30 days, none was closed by a local session. The local rung costs cloud work instead of
saving it: blocked tickets come back to orchestrator sessions, and a local failure never
escalates to the next rung (see [Escalation](#escalation-a-blocked-local-ticket-dies)).

What changes (filed as wq#2583): take `local-worker@local` off the `mechanical-edit` and
`probe-build` chains (research/models.yaml:323-324), leave `local_standard_engineering: false`,
and re-route the non-real blocked local tickets to sonnet-5. Local-worker comes back only
after a member passes the macbench agentic suite (wq#2246): at least 60% on
mechanical-edit-shaped tasks with FALSE_COMPLETE at or below 10%. The boxes stay up for
consumers outside fworker.

## AC1: request volume by model

**Gateway-side count: not readable from a build worker.** `litellm-cost` reads Langfuse
ClickHouse through `docker exec`. `docker` is not on the worker allowlist,
LiteLLM `/metrics` returns 404, and `/spend/logs` needs the master key (ISSUE-LOG #703). The
gateway number stays a Lumen-side read:
`ssh lumen@<lumen> '/home/lumen/bin/litellm-cost 30'`.

**Fleet-worker count, measured.** Every fworker session writes `attempt-N.json` with the
*resolved* model and `turns`, the number of model calls. That covers all LiteLLM traffic
from fworker, and every local-rung request comes through fworker.

| model | tickets (queue, 30d) | fworker sessions | model calls (turns) | wall h | SUCCESS sessions |
|---|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 897 | 1237 | 88,577 | 136.5 | 793 |
| claude-opus-5 | 259 | 356 | 34,464 | 81.6 | 259 |
| claude-fable-5 | 57 | 64 | 4,852 | 11.5 | 47 |
| claude-fable-5-1 | 27 | 17 | 2,100 | 4.5 | 10 |
| claude-opus-5-5 | 14 | 13 | 1,009 | 1.7 | 7 |
| hathor-llama8b | 41 | 49 | 89 | 14.3 | **0** |
| nephthys-qwen14b | 8 | 40 | 131 | 17.0 | **0** |
| qwen36-local | 14 | 67 | 126 | 4.7 | **0** |
| local-worker (group) | 40 | n/a (resolved to a member at admit) | | | |

Local models made 346 of about 131,300 fworker model calls (0.26%) and used 36.0 wall-hours
of box time to get them. By ticket count, local is 103 of about 1,360 (7.6%). By calls
that did work, it is effectively 0%.

Local session outcomes, all-time (166 sessions; the 30d window holds 156 of them, and the
hathor and nephthys counts are identical in both):

- hathor-llama8b: 32 FALSE_COMPLETE, 16 HANG, 1 KILLED. Mean 2.5 turns per FALSE_COMPLETE (80/32):
  the 8B model declares "done" after about two calls.
- nephthys-qwen14b: 24 HANG, 11 KILLED, 3 TURNS, 2 FALSE_COMPLETE.
- qwen36-local: 45 HANG, 14 CRASH, 7 FALSE_COMPLETE, 5 KILLED, 4 LAUNCH_FAILURE.
- Median worker prompt is about 5.3k tokens (`prompt_tokens_est`). At hathor's measured
  13.6 tok/s prefill (dispatcher/config.json `local_backends`), that is about 390 s before
  the first token of every turn, and context only grows from there. `first_frame_s` medians:
  hathor 1500, nephthys 2400, qwen36 900. HANG is mostly this latency.

## AC3: what local actually closes

Queue view (30d, local-model tickets): blocked 58, done 41, open 4. By archetype:

| archetype | blocked | done |
|---|---:|---:|
| mechanical-edit | 27 | 13 |
| verify-pass | 17 | 6 |
| standard-engineering | 7 | 9 |
| probe-build | 4 | 2 |
| (none) | 3 | 11 |

Who closed the 41 done tickets:

- 30 by orchestrator sessions (`claimed_by orch-*`, which are Claude sessions)
- 10 closed without a claim (duplicate or superseded sweeps)
- 1 by fworker: wq#1179, where qwen36-local was KILLED and claude-opus-5 then succeeded

Cross-checked against `_runs`: 26 of these tickets have no fworker session, 12 have only
failed sessions, and 3 were closed by claude-opus-5.

**Local closes no archetype.** The "40 done" in the audit reflects tickets filed with a local
model tuple. Cloud sessions closed them.

Block reasons (58, bucketed by `local_rung_audit.bucket`): SPAWN_LOOP 17, FALSE_COMPLETE 16,
EVIDENCE_GATE 6, HANG 4, CRASH 4, KILLED 3, FRANK 2, CITED_PATH_UNREACHABLE 2,
CAPABILITY_DENIED 1, TURNS 1, other 2.

- **The 17 SPAWN_LOOP blocks are a routing burst, not a model-quality signal.** All 17 are
  verify-pass tickets written with `model=hathor-llama8b` between 2026-09-20 22:27 and 23:30.
  That window matches the fleet-node render commits (27c1fd5 at 22:29, 84680f3, 52242ad).
  Their parents were sonnet-5 or opus-5 tickets. verify-pass is not in
  `LOCAL_ALLOWED_ARCHETYPES` (harness/fworker.py:167), so `local_rung_gate` returned DEFERRED
  before the claim every time. No `_runs/wq<id>` dir exists for any of them. After 3 spawns,
  `spawn_loop_guard` blocked them (dispatcher/spawn_loop_guard.py:46). wq#2583 re-routes them
  to sonnet-5@high.
- **Without that burst, 41 local tickets are blocked against 41 done**, and the done ones were
  still closed by cloud.

### Cost of FALSE_COMPLETE

| model | FALSE_COMPLETE sessions / all sessions | tickets hit | wall h | cycles per ticket hit |
|---|---:|---:|---:|---:|
| hathor-llama8b | 32 / 49 (65%) | 20 | 9.5 | 1.6 |
| claude-sonnet-5 | 83 / 1237 (6.7%) | 59 | 11.9 | 1.4 |
| claude-opus-5 | 14 / 356 (3.9%) | 8 | 4.2 | 1.8 |

Each FALSE_COMPLETE costs one evidence-gate replay (`verify_done`) plus one resume or fresh
attempt (fworker.py:4028). At hathor that is about 18 minutes of a 2-slot box per cycle
(9.5 h / 32). No sonnet-5 verify-pass cycle was spent on a local parent: there are 0
`[verify] wq#<local parent>` tickets, because nothing a local session authored ever reached
done. The real cost lands later, when an orchestrator session picks up the blocked ticket and
does the work from scratch.

## AC2: escalation (a blocked local ticket dies)

The manifest defines a next rung (`mechanical-edit: [local-worker@local, sonnet-5@low]`), but
nothing in production ever reaches it:

1. `dispatcher/foreman_dispatcher.py:610-616`: `candidate_model()` returns the ticket's
   stored `model` unchanged once one is set.
2. `foreman_dispatcher.py:618`: when no model is stored, it calls
   `router.assign(tid, dry_run=True)` with no `fails`. The attempt-count walk in
   `orchestrator/router.py:589-592` therefore never runs outside `router.py route --fails`.
3. `harness/fworker.py:4295-4297`: a retry reuses the same `args.model`. Once attempts run
   out, `fworker.py:4317` calls `wq block`. The resumable path (FALSE_COMPLETE/HANG,
   fworker.py:4028) resumes the same session and then falls through to that same
   `handle_failure` (fworker.py:4001).
4. Nothing in the tree rewrites `model` on a block. The only `wq edit --model` writers are
   `router.assign` and `harness/model_alias_sweep.py:81`.
5. A backend fault (`handle_rung_fault`, fworker.py:4251) releases the ticket but keeps the
   local model on it. Point 1 then sends it straight back to the same pool.

**Fix filed: wq#2581.** On terminal failure of a local-pool model, the ticket should get
`wq edit --model/--effort` set to `router.select(..., fails=1, allow_local=False)`, a
one-time `ESCALATED` marker, and a release instead of a block. REAL_BLOCK, CAPABILITY and
CITED_PATH blocks still block. The same applies to the SPAWN_LOOP block when the ticket model
is local.

## AC4: cross-checks

- **wq#2565 (`is_local_model` misses `local-worker`)**: confirmed in code, but latent on the
  dispatch path. `LOCAL_POOL_MODELS` (harness/fworker.py:161, the generated block) omits
  `local-worker`. However, the dispatcher resolves the group to a member before spawning
  (foreman_dispatcher.py:1619-1633) and passes it as `model_override` (:1873-1877).
  No attempt record in `_runs` has `"model": "local-worker"`, and none has
  MODEL_NOT_FOUND. It caused 0 of the 58 blocks. It still bites any non-dispatcher entry
  (`fworker run --ticket` on a local-worker ticket reads the group name from the ticket at
  fworker.py:3725-3737). wq#2565 itself is blocked FALSE_COMPLETE on claude-fable-5; the fix
  there is still wanted, just not urgent.
- **wq#2512 (11 failing LiteLLM groups)**: none of its hard errors are on the fworker local
  rung. The dead ones are glm-5.3-flash-local, isis-qwen3-32b, qwen38-9b-distill-local,
  ternary-bonsai-27b, mistral-7b, granite, gemini-flash, grok-4.6 and kimik3. The rung
  members hit 100 s timeouts instead: `hathor-llama8b` and `local-worker` timed out while one
  fworker (wq#2495) held hathor. That is the same prefill ceiling measured above. A single
  worker saturates the box, so health probes answering 200 say nothing about whether the box
  can serve work.

## Gaps

- Gateway-side 30d per-model counts need a Lumen-side read (`litellm-cost 30`, or a SpendLogs
  query with the master key). This is also the only way to see LiteLLM consumers outside
  fworker (LPP, n8n, mailbot) that may justify keeping the boxes up. The allowlist gap is
  ISSUE-LOG #703.
- Prior-art search could not run: `fleet-search web` returned empty results for every query,
  and WebSearch is not granted to build workers (ISSUE-LOG #703). The verdict rests on
  first-party data only.
- Queue numbers move between runs (done went 40 → 41 during this audit). Rerun the script
  rather than trusting this snapshot.
