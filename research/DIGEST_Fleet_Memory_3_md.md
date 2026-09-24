# DIGEST — Frank's "Fleet Memory 3.md" (wq#2463)

Source: `/home/lumen/scratch/TWO/Fleet Memory 3.md`, 761 lines, read in full 2026-09-24.
The doc has no numbered sections and repeats its own headings, so every citation below is
a **line range** into that file plus the heading text, e.g. `FM3 L15-20 §"Deploy 'Code
Mode' via Bifrost Gateway"`.

First of the wq#2463-2469 digest family. Sibling tickets (2464 Fleet searchability, 2465/2466
Orchestation 1-2, 2467/2468 Fleet Monitoring 1-2, 2469 Fleet up to 11) should reuse this
layout and read §7 before they start — it names the one harness trap this ticket hit.

## 1. What the doc is, and how much of it to trust

It is a raw chat transcript, not a spec. Three properties matter before acting on it:

- **Duplicated block.** `FM3 L105-151` is a verbatim copy of `L57-104` (Redpanda-vs-Kafka
  paragraph, the Helm install, "Your New Agentic Architecture"). Same content twice, not
  two recommendations.
- **Leaked assistant scratchpad.** `FM3 L607-674` is the generating model's own planning
  text — "Constraint check", "No meta-announcements", "Let's do this", a `GenerateWidget`
  placeholder id. It carries no fleet content; skip it.
- **Escape-mangled code.** Every Python block is markdown-escaped: `\_\_main\_\_`,
  `producer \= `, `\!`, `\[BACKGROUND TASK\]`, `\--set`. Pasting any of it fails. If we
  implement one of these shapes, retype it from the library docs, never copy from the doc.

Truncation: the last section stops mid-sentence at `FM3 L757` ("you should see all 3
replicas in the \""). Nothing after that but a rule and a blank line.

**Verified by independent search** (`fleet-search web`, 2026-09-24) — the two load-bearing
external claims are real, not hallucinated:

| Doc claim | Verdict | Source found |
|---|---|---|
| MCP got a stateless spec revision dated 2026-07-28 carrying protocol version in `_meta` (`FM3 L1-6`) | TRUE | `modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http` — "protocolVersion field carried in the request body's `_meta`. If the values do not match, the server MUST reject"; design work is SEP-2575 / SEP-1442 ("Make MCP Stateless (by default)") |
| Bifrost "Code Mode" replaces tool definitions with 3 meta-tools, >90% token cut (`FM3 L15-20`) | TRUE, with a scaling caveat | `docs.getbifrost.ai/mcp/code-mode` — "at around **500 tools**, Code Mode reduced average input tokens per query by roughly 14x"; third-party writeups report 50%+ "in multi-tool MCP workflows". The 90%+ figure is a ~500-tool number, not a 20-tool number |

## 2. Every actionable recommendation, with a fleet verdict

13 distinct recommendations. Verdicts: **ADOPT** (net-new, build it), **ADAPT** (idea good,
the doc's mechanism is wrong for our substrate), **HAVE** (already in production here),
**REJECT** (measured or structurally wrong for us).

| # | Recommendation (doc cite) | Verdict | Why — fleet evidence |
|---|---|---|---|
| R1 | Make MCP servers stateless; every call carries `_meta`; run them as spin-to-zero containers/functions instead of all-day sessions (`FM3 L1-6`) | **ADOPT** | Nothing in the repo tracks MCP protocol revision per server. We run long-lived MCP daemons (`desktop-commander-mcp` is in the never-restart list, `fleet-exec-policy.yaml:32`), and at least one is an unauthenticated always-on surface: `curl localhost:3104/mcp` tools/list returns 200 anonymously because supergateway ships no auth flag (`kb/ISSUE-LOG.md` #68). Stateless + spin-to-zero shrinks both the idle cost and that exposure window |
| R2 | Expose agents to each other as MCP tools (`ResearchAgent`, `DiagnosticAgent`) so the orchestrator delegates without knowing how (`FM3 L8-13`) | **HAVE** | This is fworker: the dispatcher hands a ticket to an archetype worker (`research-synthesis`, build, verify) and never models the work itself. Delegation already runs over the wq table + `--add-dir` grants, which is strictly safer than an MCP tool surface, because a ticket carries a lease, a fence, a budget and an evidence gate that a tool call does not |
| R3 | Run a Bifrost MCP gateway in Code Mode: 3 meta-tools instead of N tool schemas (`FM3 L15-20`) | **REJECT for workers** (measure first for orch, see T2) | Two blockers, both already documented. (a) The fleet already evaluated tool-schema deferral under wq#889: the headroom proxy "pulls 23-65 tool definitions out of each request, which is 92% of the savings it reports **and a change to what the model can see**" (`research/HEADROOM_EVAL.md:118`). (b) Worse, it is cache-hostile: "the tools block is cached BEFORE the system block that carries cwd, so every real fworker session does get a 16.2k-24.6k-token cross-session hit on turn 1" (`research/PREFIX_STABILITY.md:40`). A tool list that varies per prompt destroys that hit. And workers already run `--tools <restricted> --strict-mcp-config` (`harness/fworker.py:2063`, wq#2185), so there is no 500-tool problem to solve — the Bifrost 14x is a 500-tool number |
| R4 | Adversarial agent that calls your tools with bad parameters, and **rewrites the tool's own description** on failure (`FM3 L22-27`) | **ADAPT** (build the fuzzer, drop the self-rewrite) | The fuzz half is a real gap: `/home/lumen/bin` CLIs are the fleet's tool surface and nothing systematically probes their failure modes. The self-rewriting half violates instinct 7 (nothing auto-edits instruction/memory artifacts; candidates go to review) — so it files PRs, it does not mutate descriptions in place. See T3 |
| R5 | Reflexion / Actor-Critic: a Critic reads the Actor's logs, emits a specific critique, Actor retries conditioned on its own failure (`FM3 L35-40`) | **HAVE** | This is the Minerva verify judge plus verify-pass tickets: a second model re-runs the failing check and rejects a claimed done (wq#1906 "name the rung that answers the retry", wq#2460 context-authority validation, wq#1932 human-alignment correlation). `harness/acceptance.py` + the evidence gate are the machine-checked half. The critique-conditioned retry is the redispatch (`attempt`, `reopened_count` on every wq row) |
| R6 | Replace synchronous calls with a Kafka/Redpanda event backbone; agents subscribe and wake on events (`FM3 L42-47`) | **REJECT as stated**, one narrow ADAPT (T7) | The dispatcher is a systemd-timer poll loop today, and the chronic "stall" was investigated to root cause: five stacked causes, and **not one of them was a missing event bus** — a metric that counted `lane_cap` as dispatchable, and one `wq show --json` subprocess per `_runs/wq*` dir burning "171.5 s of a 197 s pass" over 1052 dirs (`research/DISPATCH_STALL_RCA.md:15,17`), against a 5-min `fast-burn.conf` cadence Frank installed 09-10 and never reverted (`:65`). Putting Redpanda in front of that bug changes the transport and keeps the bug, while adding a broker, a topic schema, and consumer-group offsets to the on-call surface |
| R7 | Strict Supervisor hierarchy with explicit limits, and an FSM that forbids e.g. Diagnosing → Rebooting without passing through Verification (`FM3 L49-55`) | **HAVE** (spend limits) + **ADOPT** (the FSM transition guard) | Spend containment exists: weekly-budget pacing (wq#716), the loop breaker, lease/fence/concurrency caps, per-lane `max_per_day`. The *state-machine* claim is the genuinely new idea and it maps onto a real house rule — verification must precede remediation. Whether the recovery ladder can actually jump diagnose→restart without a verify hop is unaudited. See T5 |
| R8 | Deploy single-node Redpanda on K3s via the Helm chart, `statefulset.replicas=1`, TLS/SASL off, Console on (`FM3 L57-95`, dup `L105-143`) | **REJECT** | There is no Kubernetes on this fleet. `which k3s kubectl helm rpk` on lumen returns **nothing** — no cluster, no Helm, no broker CLI. The fleet's orchestrator is systemd units plus SQLite (`queue.db`, `minerva.db`, `alerts.db`) and a few docker containers (`research/FLEET_SERVICE_ATLAS.md:14`). Adopting this means standing up K3s *first*, for one broker, to fix a problem R6 shows we do not have. Also note `tls.enabled=false` + `auth.sasl.enabled=false` on a fleet whose services are cloudflared-exposed |
| R9 | `aiokafka` producer/consumer; **same `group_id` load-balances, different `group_id` fans out** (`FM3 L153-237`, insight `L237`) | **NOTE** (correct, inapplicable today) | The semantics are right and worth remembering if T7 ever lands. Depends entirely on R8 |
| R10 | Run the infinite consumer loop as a FastAPI lifespan `asyncio.create_task`, never in a route; cancel on shutdown (`FM3 L239-323`, insight `L323`) | **ADOPT as a house rule**, independent of Redpanda | The general lesson — a `while True` in a request handler freezes the whole app; background loops belong in lifespan and must be cancelled on shutdown — applies to every FastAPI daemon we already run (messenger :8765, memoryd :8139, dashboard :8123, pokemon-live :8871). It also matches a bug we already paid for: wq#467 "offload `_memory_context` off the event loop" was exactly this class |
| R11 | WebSocket `ConnectionManager`, broadcast via `asyncio.gather(*tasks, return_exceptions=True)` so one slow client cannot freeze the fan-out (`FM3 L325-433`, insight `L433`) | **ADOPT if messenger fans out** | Correct and cheap. Sequential `await conn.send()` in a loop is a real footgun. Applies to `lumen-messenger :8765`, which is public via cloudflared (`research/FLEET_SERVICE_ATLAS.md:83`) |
| R12 | Multi-worker scaling: (a) Redpanda pub/sub instead of Redis to sync uvicorn workers, (b) first-message WS auth with `close(1008)` on a 3 s timeout, (c) Cloudflare Worker + KV edge token check, Durable Objects / WebSocket Hibernation on the $5 plan (`FM3 L435-473`) | (a) **REJECT** · (b) **ADOPT** · (c) **ADAPT** | (a) needs R8 and presumes multiple uvicorn workers; single-worker is our shape, so neither Redis nor Redpanda is needed. (b) is the best security idea in the document and costs a few dozen lines: accept, hold unauthenticated, require an auth frame first, sever on timeout. (c) edge auth genuinely offloads garbage traffic from the Walnut Creek uplink, but the fleet has burned a Cloudflare lesson before — the KV cap (wq#991) — and CF Access scripting is already blocked on a dead token (`kb/ISSUE-LOG.md` #37, #68). Treat as design input, not a build |
| R13 | RK3399 watcher pipeline: eBPF kernel probes → NVMe-local broker buffer → **Prompt Synthesizer** that turns an Alertmanager anomaly into a structured LLM task → worker pool consumes it (`FM3 L475-605`, worker pool `L674-757`) | **HAVE** (the synthesizer) + **ADAPT** (eBPF) + **REJECT** (the broker/K3s plumbing) | The synthesizer is `bin/aq_escalate.py`: an alert crossing threshold seeds **one** P0 fleet-fires wq ticket naming the runbook and pages once, "at-most-once side effects and idempotency ... enforced by the escalations table (UNIQUE aq_id + step-wise ticket_id/paged_at), so a transient wq/ntfy failure retries WITHOUT double-paging" (`bin/aq_escalate.py:13-21`). That is the doc's design **plus** the exactly-once property the doc's version lacks — `FM3 L543-586` re-synthesizes a prompt for every firing alert in every webhook POST with no dedup, which is a paging-storm generator. Dozens of `[fleet-fires] probe-X CRITICAL unacked x3 (aq#NNNN)` tickets in the queue are this path working. eBPF as a *sensor* is a fair idea for the watcher nodes; it is orthogonal to the transport |

## 3. Which fleet problems this actually solves

Honest scoring: of 13 recommendations, **4 are net-new work** (R1, R4-fuzzer, R7-FSM, R12b),
**3 are already in production** (R2, R5, R13-synthesizer), **2 are house rules worth
writing down** (R10, R11), and **4 are rejected on measured fleet evidence** (R3, R6, R8,
R12a).

Cross-references, per open problem:

- **wq#2389 / wq#2413 — "isis_alert has no consumer: a critical LPP page lands only in
  events/*.jsonl and stderr."** This is the one open ticket the doc speaks to directly
  (`FM3 L42-47`, `L506-511`: an event nobody subscribes to is a lost event). But the fleet
  already owns a consumer pattern for exactly this — `aq` + `aq_escalate.py` — so the fix
  is to route `isis_alert` into `aq`, not to introduce a second bus. T4.
- **wq#1819 — "rejected `rate_limit_event` never reaches the governor
  (fworker.py)."** Superficially "we need an event bus"; actually in-process plumbing, one
  process to one object. A broker would add a network hop and the same dropped event.
  Named here so nobody cites this digest as justification for R6.
- **Chronic dispatch stalls.** Answered above (R6): `DISPATCH_STALL_RCA.md` already found
  five causes, none of them transport. This doc would have cost us a K3s migration and
  left the 171.5 s subprocess fan-out in place.
- **Per-session token cost / prefix cache.** `HEADROOM_EVAL.md:118` + `PREFIX_STABILITY.md:40`
  + wq#2185 mean the fleet has already taken the wins Code Mode sells, by a cheaper route
  (restricted `--tools`, `--strict-mcp-config`, static-slab-first prompts). Claude Code
  itself now defers tool schemas natively — this very session lists `WebSearch`, `Monitor`,
  `SendMessage` and others as deferred tools fetched on demand through `ToolSearch`. We
  would be buying a gateway to re-implement a harness feature we already run.
- **MCP surface security.** `ISSUE-LOG` #68 (anonymous `tools/list`) and #37 (dead CF
  token) are both improved by R1's spin-to-zero + stateless posture: a server that is not
  running all day is not an all-day anonymous surface.
- **Verification before remediation.** R7's FSM is the doc's best structural idea and has
  no ticket today.

## 4. BUILD tickets to file

Ordered by (value / cost). Each is written so an implementer needs nothing but the ticket.

**T1 — MCP stateless-spec conformance audit (lane: research, pri 2).**
For every MCP server the fleet runs (`memory-mcp`/supergateway :3104, `desktop-commander-mcp`
:3100, `chrome-mcp.lumennode.uk`, `postgres-mcp`, `postgres-mcp-plausible`, `mcp-backend`),
record: protocol revision advertised, whether it requires the session `initialize`
handshake, whether it accepts `_meta.modelcontextprotocol/protocolVersion` per the
2026-07-28 streamable-HTTP revision, whether it is auth-gated, and idle RSS. Output a table
in `research/MCP_SERVER_CONFORMANCE.md` with a per-server verdict of
keep-warm / make-stateless / spin-to-zero / retire. Accept: table covers every server in
`FLEET_SERVICE_ATLAS.md` §4 with a cited probe per row; any server whose idle RSS exceeds
its monthly usefulness is named. Cites `FM3 L1-6`, `ISSUE-LOG` #68.

**T2 — Measure the tools-block token cost before any Code Mode work (lane: research, pri 3).**
Gate ticket, deliberately cheap. For each profile (build worker, research worker, orch
session), count the tokens in the tools block as actually sent. If a profile's tools block
is under ~4k tokens, close R3 permanently for that profile with the number recorded. Only a
profile above that threshold may propose gateway work, and it must model the prefix-cache
loss from `PREFIX_STABILITY.md:40` against the saving. Accept: one number per profile in
`research/TOOLS_BLOCK_COST.md`, plus an explicit keep/kill verdict on Bifrost Code Mode.
Cites `FM3 L15-20`, `HEADROOM_EVAL.md:118`, wq#889, wq#2185.

**T3 — Adversarial tool-tester for `/home/lumen/bin` (lane: build, pri 2).**
A harness that, for each allow-listed CLI, runs a matrix of bad invocations (missing
required arg, wrong type, nonexistent id, path outside sandbox, contradictory flags) and
records exit code + stderr. Fail a tool when it exits 0 on bad input, hangs waiting for
stdin (the headless-worker killer), or emits a stack trace instead of an actionable
message. Reuse the `cli-for-agents` skill's criteria and the existing `capability_lint`
plumbing; write results to `research/TOOL_FUZZ_REPORT.md` and open one PR per fix.
**Explicitly out of scope:** the doc's self-rewriting tool descriptions (`FM3 L26-27`) —
instinct 7 forbids self-editing instruction artifacts; findings become PRs for review.
Accept: every `/home/lumen/bin` tool in the worker allowlist has a fuzz row, and at least
the hang-on-stdin class is fixed or ticketed. Cites `FM3 L22-27`.

**T4 — Give `isis_alert` a consumer through `aq`, not a new bus (lane: build, pri 1;
supersedes the transport half of wq#2389/wq#2413).**
Publish `isis_alert` into the `alerts.db` `aq` table so `aq_escalate.py`'s existing
threshold → one-P0-ticket → page-once path picks it up, inheriting the UNIQUE-aq_id
idempotency. Accept: a synthetic `isis_alert` fired three times produces exactly one
fleet-fires ticket and one ntfy page, proven by test; no new daemon, no broker. Cites
`FM3 L42-47`/`L506-511` for the problem statement, `bin/aq_escalate.py:13-21` for the
mechanism.

**T5 — FSM transition guard: no remediation without a preceding verification state
(lane: build, pri 2).**
Audit every automated remediation path (recovery ladder, self-healer, `fleet-exec restart`,
dispatch watchdog) for whether it can act on a *stale or unverified* diagnosis, i.e. jump
diagnose → act with no re-check in between. Then encode the legal transitions explicitly
and refuse the illegal ones, the way `fleet-exec-policy.yaml` already refuses verbs
fail-closed. Accept: a documented transition table, a test that an act-without-verify
attempt is refused, and a named list of paths that were already compliant. Cites
`FM3 L49-55`.

**T6 — First-message WebSocket auth for `lumen-messenger` (lane: build, pri 2).**
Audit the public surface on :8765 (cloudflared-exposed, `FLEET_SERVICE_ATLAS.md:83`) for
how a connection is authenticated today. If a socket can reach the broadcast pool without
proving a token: accept the connection unauthenticated, require an auth frame as the first
message, and `close(1008)` if it is absent or invalid within 3 s. While in there, make any
fan-out use `asyncio.gather(..., return_exceptions=True)` rather than a sequential await
loop, and ensure any background loop lives in the lifespan handler with cancellation.
Accept: a test that an unauthenticated socket is closed with 1008 inside the timeout and
never receives a broadcast; existing clients still connect. Cites `FM3 L447-453` (auth),
`L433` (gather), `L239-323` (lifespan).

**T7 — SPIKE, timeboxed, blocked on T4+T5: does anything here need a real event log?
(lane: research, pri 4).**
Only if T4 and T5 surface a genuine multi-consumer or replay requirement. Then the answer
to evaluate is **an `events` table in an existing SQLite db with a cursor per consumer** —
not Redpanda, not K3s. Compare on: consumers needed, replay window, delivery guarantee,
operational surface added. Accept: a recommendation with a number for each, and an explicit
"do nothing" option costed. Cites `FM3 L42-47`, `DISPATCH_STALL_RCA.md:15,17,65`.

**T8 — `capability_lint` path extraction must handle paths containing spaces
(lane: build, pri 2).**
Filed from this ticket's own failure — see §7. Accept: a ticket whose detail cites
`/home/lumen/scratch/TWO/Fleet Memory 3.md` derives the containing directory (not
`/home/lumen/scratch/TWO/Fleet`) and either emits the correct `needs:dir` or no finding;
regression test with a spaced path and a quoted path. Sibling tickets wq#2464-2469 hit
this the moment they dispatch.

## 5. What to STOP doing / never start

1. **Do not stand up K3s.** Not for a broker, not for a worker pool. `which k3s kubectl
   helm rpk` → nothing; systemd + SQLite is the substrate and it is load-bearing across
   ~171 timers. Every Helm/`kubectl`/Deployment snippet in `FM3 L57-95, L666-757` presumes
   a cluster that does not exist. (`FM3 L741` even sizes replicas against "Dell
   Wyse/OptiPlex nodes" — hardware inventory, not a cluster.)
2. **Do not build or buy an MCP gateway for token reduction.** Measured already (wq#889,
   `HEADROOM_EVAL.md:118`), cache-hostile here (`PREFIX_STABILITY.md:40`), and the harness
   ships schema deferral natively. T2 exists to kill this idea with a number, not to revive it.
3. **Do not build a second Critic.** Minerva verify + verify-pass + the evidence gate are
   the Reflexion loop (`FM3 L35-40`); adding a parallel critic doubles judge spend and
   splits the alignment metrics wq#1932 tracks.
4. **Do not build a prompt synthesizer.** `bin/aq_escalate.py` is it, and it is *better*
   than the doc's — the doc's webhook (`FM3 L543-586`) publishes one task per firing alert
   per POST with no dedup key, which under Alertmanager's `repeat_interval: 1h`
   (`FM3 L598`) is a self-inflicted paging storm. Ours enforces at-most-once on a UNIQUE
   `aq_id`.
5. **Do not add Redis (or Redpanda) for cross-worker fan-out.** Both presume multiple
   uvicorn workers; we run single-worker daemons. The doc reaches this conclusion for Redis
   itself (`FM3 L441`) — the same argument retires its own replacement.
6. **Do not copy code out of this or any of Frank's research docs.** Every block is
   markdown-escaped and will not run. Retype from upstream docs.
7. **Stop treating a research doc as a plan.** This one is ~40% duplicated text, leaked
   scratchpad and mangled code, and its central recommendation (event bus on K3s) is
   falsified by an RCA we already wrote. The digest step is what makes these docs usable —
   which is the argument for finishing wq#2464-2469 rather than acting off any one doc raw.

## 6. The one-line version for Frank

Four things worth building (MCP stateless audit, CLI fuzzer, verify-before-remediate FSM,
WebSocket first-message auth), three things we already have and should stop re-inventing
(delegation, critic, alert→task synthesizer), and one expensive wrong turn to skip
(Redpanda on a K3s cluster we do not run, to fix a stall whose five real causes are already
documented and mostly fixed).

## 7. Harness note for the sibling digest tickets (wq#2464-2469)

The doc lives in `/home/lumen/scratch/TWO/`, which is **not** in a research worker's
`--add-dir` set. Consequences, measured this run:

- `grep`/`cp` against that path are refused ("may only search for patterns in files from
  the allowed working directories"). Do not retry a synonym (`cat`, `sed`, `head`) — the
  path is the problem, and retrying is what trips the loop breaker (wq#1107).
- **The `Read` tool reaches it.** Read the doc with `Read(file_path=..., offset, limit)` in
  bounded chunks and the ticket is workable as dispatched. This refines `ISSUE-LOG` #646:
  the sandbox bounds *path arguments to shell commands* more tightly than it bounds Read.
- The dispatcher's advisory on wq#2463 said `cited path /home/lumen/scratch/TWO/Fleet does
  not exist (not guessed)` — it truncated at the first space in the filename, which is why
  no `needs:dir` grant was derived. That is T8.

Refused-but-legitimate command shapes from this run, for allowlist review:
`docker ps` and `docker ps --format '{{.Names}}'` were both denied despite
`"Bash(docker ps*)"` being present in `harness/worker-settings.json:22` — so a worker
cannot enumerate containers, and the container inventory in §2/R8 rests on
`FLEET_SERVICE_ATLAS.md` plus `which`, not a live probe.
