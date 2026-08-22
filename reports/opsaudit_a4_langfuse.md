# Wiring the idle Langfuse (ops-audit A4 / wq#754)

Audit finding F5: `langfuse-web`/`worker`/`postgres`/`redis`/`minio` had been up 13 days
with zero traces — the dispatcher logged `langfuse_disabled — no keys at LANGFUSE_ENV`
every pass because `/home/lumen/.secrets/langfuse-foreman.env` never existed. The
ingestion code in `foreman_dispatcher.py` (`emit_langfuse`) was already written and
tested (best-effort, seam at `LANGFUSE_EMIT`); it just had nothing to authenticate with.

## 1. What was created

- **Langfuse project `foreman`** (org `lumen`, the same org `lumen-pipeline` already
  lives in — see `/home/lumen/langfuse/DEPLOYMENT.md`), created via the authenticated
  session API (`projects.create` / `projectApiKeys.create`) rather than the
  `LANGFUSE_INIT_*` compose bootstrap, since that only runs once on first container
  start and `lumen-pipeline` had already claimed it. Separate project from
  `lumen-pipeline` on purpose: fleet-ops telemetry (dispatcher passes, fworker attempts)
  is a different consumer than the awareness/research pipeline that project already
  carries, and mixing them would make neither dashboard useful.
- **`/home/lumen/.secrets/langfuse-foreman.env`**, mode 0600, same three-line shape as
  `foreman_dispatcher.py:_load_langfuse_creds` expects (`LANGFUSE_HOST`,
  `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`), host set to `http://127.0.0.1:3020`
  (the dispatcher and fworker both run on Lumen's host, not inside the
  `langfuse_default` docker network, so the published loopback port is correct — the
  `langfuse-web:3000` in-network hostname other containers use, e.g. litellm, would not
  resolve here).
- No new infrastructure. No second observability platform. Per the audit's explicit
  rejection of that pattern (§3.6), this ticket only wired keys into what was already
  running.

## 2. Dispatcher — verified with real passes

`emit_langfuse` needed no code change; it degrades to `log-only` when the env file is
absent and posts a real `trace-create` + one `span-create` per logged ticket decision
when it's present. Verified by running `foreman_dispatcher.run_pass(dry_run=True)`
three times against the live wq queue (dry-run: real scan/decision logic, zero spawn
side effects) plus one earlier direct `emit_langfuse()` smoke call:

| trace id | when | shape |
|---|---|---|
| `e9070be9-b3ec-4224-bc72-a534489ea9a9` | smoke call | 1 span |
| `fdisp-20260822T053932Z-1214055` | real dry-run pass | 3 `would_spawn` spans |
| `fdisp-20260822T053935Z-1214055` | real dry-run pass | 3 `would_spawn` spans |
| `fdisp-20260822T053937Z-1214055` | real dry-run pass | 3 `would_spawn` spans |

All four confirmed present via `GET /api/public/traces` against the new project keys —
4 real traces, exceeding the ticket's ≥3 bar. The standing 20-minute dispatcher timer
(audit F4) will keep landing real `spawn` (not just `would_spawn`) traces from here on
with no further action.

## 3. fworker — extended to trace per-attempt spans

The dispatcher only ever sees `spawn` (ticket/lane/model/repo/unit) — it hands off to
fworker and never learns how the run went. The debugging value this ticket asked for
(archetype, effort, attempt count, outcome class, cost, "debuggable from the trace
instead of by grepping jsonl") only exists at the fworker level, which already computes
all of it into `attempt_record` for `write_attempt_state`. Changes:

- `harness/classifier.py`: `RunState` now also captures `total_cost_usd`, `duration_ms`,
  `num_turns` off the terminal `result` frame (observability only — never fed into
  `classify()`, same non-load-bearing contract as the CPU/OOM corroboration fields).
- `harness/langfuse_client.py` (new): the ingestion HTTP call factored out of
  `foreman_dispatcher.py` so fworker doesn't duplicate it — `load_creds`, `ingest`,
  `emit_trace_with_span`. `foreman_dispatcher.py` itself is untouched (its inline
  version already works and is covered by `test_dispatcher.py`; not worth the
  regression risk of a mid-campaign refactor for cosmetic dedup).
- `harness/fworker.py`: `emit_attempt_span(ticket_id, lane, attempt_record)`, called
  once per attempt right after `write_attempt_state`. One trace per ticket
  (`fworker-wq{ticket_id}`, stable across retries so every attempt lands as a new span
  on the same trace), span named `attempt-{n}:{classification}`, metadata carrying
  exactly the fields the ticket asked for: ticket, lane, model, effort, archetype,
  attempt number, outcome class (+ raw classification, exit code, turns, elapsed_s,
  cost, session_id for RCA). Best-effort: any failure logs and falls through, same
  contract as the dispatcher's `emit_langfuse` — telemetry can never fail a ticket.

Verified end-to-end by calling `fworker.emit_attempt_span()` directly with a realistic
synthetic attempt record (ticket 999999, deliberately outside the real ticket range) and
confirming via `GET /api/public/traces/fworker-wq999999` that the trace landed with the
full field set (`ticket`, `lane`, `model`, `effort`, `archetype`, `attempt`,
`outcome_class`, `total_cost_usd`, ...). Left in place as a smoke-test artifact, same
precedent as the sibling `lumen-pipeline` project's documented deploy-verification
traces.

**Cost caveat:** `total_cost_usd` is the Claude CLI's own model-pricing estimate off the
`result` frame, not a real invoice line — the fleet runs on the Max subscription, not
per-token billing (audit R2). Treat it as a relative signal (which tickets/archetypes
are expensive to retry) not a dollar figure to reconcile against a bill.

Not done: a completion-time trace for the FULL ticket lifecycle (claim → all attempts →
close) — the per-attempt span already gives per-attempt RCA, and attempts share a trace
id, so the full lifecycle is already visible as "all spans on trace
`fworker-wq{id}`" without a separate rollup event. Adding one would be duplicate
information for no new debugging power.

## 4. Two telemetry paths — what each answers

| | `claude_code.*` in VictoriaMetrics | Langfuse (this ticket) |
|---|---|---|
| **Shape** | Numeric time series (OTLP metrics, pushed by every `claude -p` child fworker spawns — `harness/fworker.py:build_env` sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://100.110.156.91:8429/opentelemetry`, Bes) | Structured traces/spans with full metadata payloads |
| **Answers** | "How much/how fast, in aggregate, over time?" — token/cost counters, latency histograms, error rates, dashboarded and alertable in Grafana | "What happened on THIS ticket's THIS attempt, and why did it end up TURNS/FALSE_COMPLETE/KILLED?" |
| **Granularity** | Per-metric-name series, no per-ticket join without external correlation | Per-attempt, with ticket/lane/model/archetype/outcome as first-class metadata |
| **Retention/query** | PromQL, Grafana panels, long retention | Langfuse UI trace explorer, `/api/public/traces` filters |

**Recommendation — one place per question:**

- **"Is the fleet healthy right now / trending well?"** → Grafana on VictoriaMetrics.
  Aggregate dashboards are what PromQL is for; Langfuse's UI is not built for
  fleet-wide time-series rollups.
- **"Why did wq#NNNN fail, and what did that attempt actually cost?"** → Langfuse,
  trace `fworker-wq{NNNN}`. This is the debugging path the ticket asked for and the
  VM metrics cannot answer at all (no per-ticket dimension).

They are not the same data re-shaped twice (audit's "two half-wired paths" framing) —
metrics answer aggregate/trend questions, traces answer per-run RCA questions. Both
being wired is the fix, not a redundancy to collapse; the redundancy risk the audit
flagged was two *dashboards* answering the same question with no single source of
truth, which the table above closes by assigning each question exactly one home.

## 5. Open items (not in this ticket's scope)

- The dispatcher's own `spawn` event (pre-run) still doesn't carry effort/archetype —
  only fworker's post-run spans do, since effort/archetype live on the wq ticket record
  fworker reads (`resolve_tuple_from_ticket`), not something the dispatcher's pre-spawn
  decision loop resolves today. Not needed: the fworker span is the debugging surface
  that matters, and it already has everything.
- No Langfuse evaluators/dashboards/alerts built for the `foreman` project (the sibling
  `lumen-pipeline` project's `DEPLOYMENT.md` §6/§7 documents the same gap for that
  project as "UI work, open"). Out of scope here — this ticket's bar was traces
  visible, not a full observability program.
