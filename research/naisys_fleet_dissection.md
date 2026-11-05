# NAISYS Dissection — Cross-Machine Agent Runner Patterns for the Fleet

**Ticket:** wq#4863 · **Date:** 2026-11-05
**Source:** https://github.com/swax/NAISYS (MIT, Node.js >= 22, Turbo monorepo, shallow clone reviewed at `/tmp/fworker-wq4863/clone/naisys`)
**Docs read:** `docs/000-architecture.md`, `docs/004-start-agents-on-mail.md`, `docs/005-multi-machine-redux.md`, `docs/006-erp-orders.md`, `docs/012-hateoas.md`, `docs/017-ephemeral-subagents.md`, `docs/018-continuity.md`, `apps/hub/README.md`, `apps/erp/README.md`, `apps/supervisor/README.md`, `apps/naisys/src/agent/agentConfig.ts`, `apps/naisys/src/command/commandProtection.ts`

## What NAISYS is

Four npm packages, each a binary and a programmatic export, chainable into one
process (`naisys --integrated-hub --supervisor --erp`) or distributed across
machines:

| Package | Role | Fleet analogue |
| --- | --- | --- |
| `naisys` (runner) | Proxies a real shell to an LLM; `ns-*` commands (mail, web, images, desktop); cost/context limits | fworker / Claude Code session on a node |
| `@naisys/hub` | Fastify + Socket.IO server; owns ALL persistence (mail, logs, cost, config) | Lumen (PostgreSQL + foreman) |
| `@naisys/supervisor` | Web UI; monitoring, users, permissions | FleetView / Grafana |
| `@naisys/erp` | HATEOAS task system: orders → operations → steps | wq + foreman dispatch |

Key architectural verdict up front: **NAISYS's strongest idea is that runners are
100% stateless and the hub owns every byte of persistent state.** Its second
strongest is the HATEOAS agent-facing API. Both map cleanly onto fleet gaps.

## 1. Hub/client architecture — state ownership (docs/005)

NAISYS v1 had multi-master DB sync (every runner had its own SQLite, synced
through the hub). They ripped it out and wrote a candid post-mortem: "too clever
by half... not maintainable." The redesign:

- **Hub is the sole authority.** Runners have *no database at all* — no SQLite,
  no Prisma import. Local mode is purely in-memory and ephemeral; hub mode
  round-trips everything over Socket.IO.
- **Hosts are pre-registered rows**, not auto-registered on first connect. Each
  host gets its own access key; hub stores only `sha256(key)` in
  `hosts.access_key_hash` and resolves identity by hash at handshake. No global
  shared secret, no hostname round-trip. Unknown keys are rejected outright.
- **`user_hosts` mapping** pins agents to hosts (GPU box, Windows box); an agent
  with no rows can run anywhere non-restricted. `restricted=true` hosts accept
  only explicitly assigned agents.
- **Disconnect = pause, not drift.** `costTracker.checkSpendLimit()` throws when
  the hub socket is down, blocking all LLM calls. Fire-and-forget buffers (logs
  1s, costs 2s) hold in memory and flush on reconnect. Because the runner holds
  no state, reconnect needs *no catch-up protocol* — re-auth, flush, resume.

**Fleet mapping.** We already have the hub shape (Lumen owns PostgreSQL, wq,
foreman) but our workers are not stateless: worktrees, busy keys, scratch files,
and local logs all live on the executing node. The transferable pieces:

- *Per-host hashed access keys* instead of one shared credential per service.
  Our Tailscale mesh authenticates machines, but service-level auth (gitea
  tokens, LiteLLM keys) is mostly shared-secret. A `hosts.access_key_hash`
  pattern + per-host key rotation (`host_rekeyed` force-disconnect) is cheap to
  adopt in foreman's dispatch API.
- *Disconnect-pauses-spend* is an elegant failsafe: a worker that can't reach
  the hub can't burn tokens. Fleet equivalent: fworker refusing to start a turn
  when Lumen's PostgreSQL is unreachable — worth adding; today a worker with a
  dead DB connection can keep spending via the gateway.

## 2. HATEOAS agent-facing REST (docs/012) — the headline pattern

NAISYS explicitly positions HATEOAS **against MCP-style tool catalogs**: instead
of front-loading hundreds of tool definitions into the prompt, the agent gets a
~10-link root (`GET /erp/api/`) and discovers only the branch it needs. Each
response carries up to four envelope members:

- `_links` — navigation (self/parent/pagination/related)
- `_actions` — what *this caller* can do to *this resource right now*:
  `{rel, href, method, title, schema, body, disabled, disabledReason}`
- `_linkTemplates` / `_actionTemplates` — factored-out patterns for list items,
  so a uniform action isn't repeated per row (saves hundreds of tokens/response)

Token-economy details that matter for agent consumers:

- **Slim list rows, rich detail resources** — `_actions` never appear on list
  items, only after navigating in.
- **Inline `body` stubs** on actions mean the agent usually never fetches the
  JSON Schema; `schema` URL is the fallback for complex bodies.
- **Mutations return slim bodies** (`{success, message, id}`) by default; UI
  clients opt into full echo with `Prefer: return=representation` (RFC 7240).
- **Disabled-with-reason, not hidden**: every logically-possible action is
  emitted with `disabled: true` + `disabledReason` ("Stop the agent before
  archiving") — the reason doubles as recourse, teaching the agent its next
  step. Only two omission exceptions: state-inapplicable actions
  (`visibleWhen`) and destructive actions the caller has no permission for
  (`hideWithoutPermission`).
- Zod is the single schema source → Fastify validation, response trimming,
  OpenAPI, per-endpoint JSON Schema endpoint, and the `action.schema` pointer
  all derive from it (`packages/common/src/hateoas-types.ts`, `hateoas.ts`
  `resolveActions()` resolver).

**Fleet mapping.** This is the pattern the ticket flags as missing. Foreman's
dispatch and wq are CLI-first; the REST surfaces we do have are bespoke
endpoints a worker must know a priori. Concrete adoption path:

1. Give foreman a `GET /api/` root returning `_links` to tickets, lanes, hosts.
2. Ticket detail responses carry `_actions`: `claim`, `block`, `done`,
   `release` — each disabled-with-reason when the gate isn't satisfied
   ("evidence.json missing AC1" as a `disabledReason` would replace a whole
   class of worker guesswork).
3. Reuse Zod-per-action with a `/schemas/:name` endpoint so prompts stop
   embedding wq flag documentation.

The `disabledReason` idea is the cheapest high-value steal: it converts server
state-machine knowledge into in-band agent hints.

## 3. Inter-agent mail vs wq tickets (docs/004, hub README)

NAISYS mail is a message bus with persistence, not a work queue:

- `ns-mail send/list/peek/mark_read/archive/search` → Socket.IO events
  (`mail_send`, `mail_list`, ...) handled by `hubMailService`; hub resolves
  usernames, persists, pushes `mail_received` to hosts running recipients.
- **Auto-start on mail** is the killer lifecycle feature: agents aren't
  spawned, they're *mailed*. `hubMailService` polls every 10s (plus reacts to
  `MAIL_SEND` and `CLIENT_CONNECTED`) for users with unread mail who aren't
  active, runs spend-limit checks, then `tryStartAgent()` → least-loaded
  eligible host. An agent finishes via `ns-session complete`, gated on having
  just sent a message so no hand-off is silently dropped.
- Manual `ns-agent start` and auto-start share one `AGENT_START` event; manual
  starts also generate an "Agent Start" mail so the action is visible in the
  thread — audit trail for free.
- Mail supports attachments; a redaction service scrubs sensitive variables
  before anything hits the DB.

**Fleet mapping.** wq is closer to NAISYS's ERP than its mail; we have no
lightweight agent-to-agent channel short of filing a ticket. The auto-start
loop, though, is directly comparable to fworker dispatch — and NAISYS's
"poll + react to events + sweep on host-connect" triple trigger is more robust
than pure polling. The `CLIENT_CONNECTED` sweep (when a host comes online,
immediately dispatch pending work assigned to it) is worth copying into
foreman: today a node rejoining Tailscale waits for the next dispatch cycle.

## 4. Agent YAML config (agents/*.yaml, agentConfig.ts)

Declarative per-agent config, e.g. `agents/assistant.yaml`: `username`, `title`,
`shellModel`, `agentPrompt` (with `${agent.username}` template vars resolved
against agent + env variable maps), `tokenMax`, `debugPauseSeconds`,
`spendLimitDollars`, `webEnabled`. Full field set from
`apps/naisys/src/agent/agentConfig.ts`: `spendLimitDollars` / `spendLimitHours`
(startup *fails* if no agent-level or global dollar limit exists),
`mailEnabled`, `chatEnabled`, `browserEnabled`, `wakeOnMessage`,
`initialCommands[]`, `commandProtection`, `multipleCommandsEnabled`,
`workspacesEnabled`, `supervisorApiHints`.

**Command protection** (`commandProtection.ts`) is a four-level ladder:
`none` → `manual` (human Y/n confirm) → `semi-auto` → `auto`. The auto modes
run every shell command through a *second LLM call* with a read-only-validator
system prompt (deny writes, pipes-to-write, chaining where any part is denied,
"when in doubt, DENY"), with the agent's own prompt inlined as context so the
validator judges role-appropriateness; `semi-auto` escalates denials to a human
instead of hard-failing.

**Fleet mapping.** Our worker definitions + lumen-change-safety already cover
most of this (deny-list hook ≈ their validator; wq spend ceilings ≈
`spendLimitDollars`). Differences worth noting:

- NAISYS makes the spend limit **mandatory at config load** — refusing to boot
  an agent with no dollar cap is stricter than our setup and cheap to mirror in
  fworker's lane config.
- LLM-as-command-validator with the agent's role prompt as context is a softer,
  role-aware layer our static allowlist can't express ("is this command
  reasonable *for this agent's job*"). Candidate for lumen-change-safety's
  semi-auto tier rather than a replacement for the deny-list.
- `initialCommands` as declarative config ≈ our CLAUDE.md busy-check protocol,
  but enforced by the runner instead of by prompt discipline.

## 5. ERP work-consumption model (docs/006, apps/erp)

Two-track hierarchy: a **plan track** (Order → OrderRevision → Operation → Step
→ Field) describing what should happen, and an **execution track** (OrderRun →
OperationRun → StepRun → FieldValue → LaborTicket) recording what did. Niceties:

- Operations carry predecessor/successor dependencies with automatic unblocking
  on completion; cost rollup on completion.
- **Labor tickets** gate operations and meter time/money per op per session —
  the agent must open a ticket to work and the spend lands on the op.
- **Work centers** gate who may complete which ops (with admin bypass);
  auto-assign on start; dispatch page sorted by due date.
- Order *revisions* with diffs and copy-on-new-rev — plans are versioned, runs
  pin to a revision.
- Batch GET/PUT endpoints for fields/steps/ops keep agent round-trips low.

**Fleet mapping.** wq tickets are flat; foreman acceptance criteria approximate
Steps. The plan/execution split is the structural idea worth stealing: wq#
tickets currently mutate in place, so "what was asked" and "what happened" blur
(our redispatch-with-prior-attempt-notes is a manual reconstruction of
OrderRun history). A `ticket_runs` table — one row per dispatch attempt, with
per-criterion field values — would give foreman the same replayable audit trail
and make the critic's evidence check a query instead of a diff archaeology
session. Labor tickets ≈ our per-ticket token accounting, but *gating* (can't
work an op without an open ticket) is stronger than our after-the-fact ledger.

## 6. Cross-machine coordination (docs/005, 017)

- All hub↔runner traffic is Socket.IO native events with acks (no custom
  JSON-RPC): fire-and-forget for high-frequency writes (`log_write` ~1s,
  `cost_write` ~2s, `heartbeat` 2s), request-response for mail/session/agent
  control, hub-push for config (`users_updated`, `models_updated`,
  `variables_updated`), status (`agents_status`), and enforcement
  (`cost_control`).
- **Two-hop agent control**: requester → hub → target host. `ns-agent start`
  on Thor can start an agent on Thoth; the hub picks the least-loaded eligible
  host from heartbeat data. Supervisor debug commands
  (`agent_run_command`, `agent_peek`) ride the same relay.
- **Cost control is push, not poll** — no network round-trip on the LLM hot
  path. Hub watches `cost_write` batches, pushes `cost_control {enabled,
  reason}` only when a limit trips or clears.
- **Ephemeral subagents** (docs/017) deliberately do *not* exist as hub users:
  in-memory only, negative synthetic userIds, activity re-stamped under the
  parent's userId/runId with a `subagentId` discriminator. "Local in identity,
  visible in activity." They dropped a prior yaml-file-per-subagent +
  process-per-subagent design as isolation nobody needed.
- **Continuity** (docs/018): per-agent `fresh`/`summary`/`full` restart policy
  with a compact-log-row cursor (`users.compact_log_id`), so resume state is
  provably current rather than a maybe-stale text column — their v1 made
  exactly the mistake our busy-key protocol guards against (resuming from a
  snapshot hours behind reality).

**Fleet mapping.** Our equivalent of `cost_control` push is nothing — spend
checks are local. Our equivalent of two-hop control is SSH. The parts worth
taking: least-loaded host selection from heartbeat data (foreman currently
pins lanes statically), and the subagent identity rule (fleet subagents
spawned by workers should stamp parent ticket/worker IDs onto their telemetry
rather than registering as fleet citizens — we mostly do this by convention;
NAISYS does it structurally).

## 7. Supervisor UI patterns (apps/supervisor)

Socket-pushed live updates everywhere (no polling); permission-aware UI driven
by the *same* HATEOAS gates as the API — buttons render from `_actions`, grey
out on `disabled`, tooltip the `disabledReason`, so no client-side permission
logic duplicates the server. Pages: agent CRUD with config revision history and
yaml import; runs with live logs and fold-out console; mail as chat UI; hosts
with env info and remote rekey; cost graphs; passkey/API-key user management.
The OpenAPI/Scalar reference is auth-gated and *hidden from agents* — agents
are expected to use HATEOAS discovery, not spec dumps.

**Fleet mapping.** FleetView covers monitoring; what it lacks is the
"UI renders the API's `_actions`" discipline, which only pays off if we adopt
the HATEOAS envelope (pattern 2). The config-revision-history on agent configs
is a small, independently stealable feature for worker lane definitions.

## Adoption recommendations (ranked)

1. **HATEOAS envelope on foreman's dispatch API** — `_links`/`_actions` with
   `disabled`+`disabledReason`, slim mutations, Zod-derived per-action schemas.
   Highest leverage: converts prompt documentation into in-band discovery.
2. **`ticket_runs` plan/execution split in wq** — one execution row per
   dispatch attempt, mirroring Order/OrderRun. Makes redispatch history and
   evidence checks first-class data.
3. **Host-connect dispatch sweep + least-loaded host pick** in foreman — copy
   the `CLIENT_CONNECTED` trigger and heartbeat-based placement.
4. **Hub-unreachable ⇒ spend-blocked** in fworker — refuse LLM turns when
   Lumen's DB/queue is unreachable, mirroring `checkSpendLimit()` throw.
5. **Mandatory spend cap at lane-config load** — fail fast like
   `agentConfig.ts` does when `spendLimitDollars` is absent.
6. **Per-host hashed access keys with rekey-and-force-disconnect** for
   fleet-internal service auth.
7. *(Lower priority)* LLM role-aware command validator as a semi-auto tier in
   lumen-change-safety; config revision history on worker definitions.

## What NOT to copy

- Socket.IO as the transport — our SSH + Tailscale + PostgreSQL substrate
  already covers connectivity; adding a WebSocket bus would duplicate it.
- ERP's full manufacturing vocabulary (work centers, item instances,
  field sets) — overkill for fleet tickets; take the plan/run split only.
- Single-process integrated mode — Lumen's services are already systemd units;
  dynamic-import chaining solves an npm-distribution problem we don't have.
- Mail as a separate channel from tickets — two queues to check is context
  overhead; keep wq as the single channel and adopt mail's *triggers*, not its
  existence.
