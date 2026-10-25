# wq#4628 — Trigger.dev as a durable execution backend for overnight fworker batches

## Problem
Overnight fworkers are fire-and-forget Claude chats: a rate-limit, timeout, or
process death stops the batch, and the remaining tickets must be manually
redispatched. We want a server-side layer that owns the queue, retry,
checkpoint/resume, and timeout policy so a dead chat loses at most its current
step, not the whole run.

## What Trigger.dev is
Open-source (MIT) durable task-execution platform. A TypeScript SDK defines
`task()` functions; a central server (webapp + "run engine" + Postgres +
Redis + ClickHouse for analytics) schedules runs, retries failed steps, and
resumes long-running tasks across restarts. v4 (current self-hosted line)
moved task execution into a "supervisor" that launches Docker containers per
run, so the control plane and run isolation are separate processes —
confirmed by Railway's self-hosting note that v4 "requires a supervisor
service to execute tasks in Docker containers" (their platform can't run it
for that reason) and Trigger.dev's own "How it works" doc: "the code is
packaged up into a Docker image and deployed to your Trigger.dev instance."

## Self-hosted deployment (Docker)
- Official path: `docker compose` stack from trigger.dev's self-hosting repo
  (webapp, Postgres, Redis, ClickHouse, Electric, Supervisor, Registry,
  Object Store). v4 also ships a Kubernetes/Helm option as of their
  2026-06-26 blog post, for teams that don't want to run the supervisor as a
  bare Docker daemon.
- State backend: Postgres is a hard dependency (Prisma ORM; `DATABASE_URL`
  env var, `directUrl` for migrations) — this overlaps with the pgvector
  Postgres we already run, but Trigger.dev wants its own schema/database, not
  a shared one, and it additionally requires Redis (run queue) and ClickHouse
  (run/event analytics). That's 3 extra stateful services beyond what we run
  today, not zero.
- Resource overhead on Lumen/MS-01: the compose stack alone (webapp + redis +
  clickhouse + electric + registry + object store) is a multi-container,
  multi-GB-RAM footprint even idle, before any run containers spin up —
  heavier than a single-binary queue. This needs a real measurement (spin up
  the compose file and read `docker stats`) before committing resources; not
  done in this pass (see Gaps).

## TypeScript SDK / calling Claude
Tasks are plain TS functions decorated with Trigger.dev's `task()`; inside a
task body you can call anything reachable from Node, including an HTTP call
to the Claude API via the Anthropic SDK, or `exec`/`spawn` of `claude -p` as
a subprocess inside the run's Docker container. There's no native "Claude
step" — it's just arbitrary code with Trigger.dev handling retry/backoff
around the task boundary. Checkpoint/resume applies at the task-attempt
level, not mid-subprocess: if `claude -p` is killed mid-run, Trigger.dev
retries the whole task attempt (per its retry policy), it does not resume the
CLI invocation itself. For wiring into our fworker dispatcher, each ticket
dispatch would become a Trigger.dev task that shells out to `claude -p`
(or calls the API directly) and the task's retry/timeout config replaces
fworker's own loop-breaker logic for that step.

## Webhook-triggered continuation
Trigger.dev tasks support `wait.forToken`/event-based resumption: a run can
pause and resume when an external webhook posts a completion token. That's
the piece that maps to "resume a multi-hour build after a human or another
system unblocks it" rather than to crash recovery — crash recovery is just
retry policy on the task attempt.

## Comparison to Temporal / Inngest / BullMQ
- **Temporal**: deterministic-replay workflow engine — replays workflow code
  from history to resume exactly where it left off, including inside a
  single function body, not just at task boundaries. Stronger durability
  model than Trigger.dev but requires writing workflow code with
  Temporal's determinism constraints (no raw I/O inside workflow code) and
  running its own server + datastore (Cassandra/Postgres/MySQL +
  Elasticsearch). Heavier to adopt, strongest guarantees.
- **Inngest**: similar developer surface to Trigger.dev (functions +
  step.run), offers a hosted and self-hosted (dev-server-based) mode; lighter
  self-hosting story but the production self-host story is less mature than
  Trigger.dev's documented Docker/K8s path.
- **BullMQ**: a Redis-backed job queue library, not a platform — no server,
  no UI, no built-in checkpoint/resume semantics beyond job retry/backoff. We
  already have the operational pattern (a Redis-backed queue) in-house via
  work-queue (wq); BullMQ would mean building the retry/timeout/resume policy
  ourselves on top of Redis, whereas Trigger.dev/Temporal/Inngest ship that
  policy layer.
- All three "real" options (Temporal, Inngest, Trigger.dev) require Node/TS
  or language-specific SDKs and a non-trivial self-hosted control plane;
  BullMQ is the only one that's "just a library" and therefore the cheapest
  to bolt onto the existing wq Postgres/Redis footprint, at the cost of us
  owning the durability semantics fworker actually needs (checkpoint/resume,
  server-side timeout).

## Recommendation (not yet actioned)
Given we already run a Postgres+pgvector backend and a custom wq dispatcher,
the lowest-overhead path to "server-side retry + resume" is extending wq's
existing Postgres-backed queue with task-attempt/checkpoint columns rather
than standing up Trigger.dev's multi-container stack (webapp+Redis+ClickHouse
+Electric+Registry+ObjectStore) for a single self-hosted node. Trigger.dev
is the right call only if we want its UI/observability and webhook-resume
semantics badly enough to absorb 3 new stateful services; that tradeoff
should go to Frank as a decision, not be assumed here.

## Gaps / not verified in this pass
- No live `docker compose up` was run against the self-hosting repo to
  measure actual RAM/CPU on Lumen or MS-01 hardware — the overhead claim
  above is architectural (container count), not measured.
- Pricing/licensing terms for Trigger.dev's self-hosted tier were not
  re-verified against their current docs beyond what the search snippets
  showed (MIT core, cloud tier for hosted).
