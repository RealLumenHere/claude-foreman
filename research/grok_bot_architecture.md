# wq#4853: Grok Bot architecture and self-hostable agent-teammate patterns

Fetched 2026-11-05. Every external fact has its URL next to it. Anything marked
UNVERIFIED comes from training knowledge, not from a fetched page.

## TL;DR

- **The architecture is already public.** Someone imaged a live Grok Bot box from
  its own terminal and published a detailed reconstruction: 1,804 recovered modules,
  process tree, ports, protocols and persistence layout
  ([gist wtfsayo/b80f74f4](https://gist.github.com/wtfsayo/b80f74f4f64d18ced8c84502a23148fe)).
  Frank's idea ("use Grok Bot to recreate Grok Bot") has already been done. We don't
  need to pay to learn the stack.
- **The stack is ordinary.** It runs as one Docker-style container per user account,
  not one per Bot. Inside are a Node "SandHost" coordinator (HTTP command gateway plus
  an SSE event stream), SQLite per agent, an `exec-daemon` for shell, PTY/tmux, MCP and
  X11, and Xvfb + Chrome + VNC/noVNC desktops. Inference is a remote service reached
  with a short-lived token. Every piece has an open-source equivalent we can run.
- **We can self-host it.** The coordinator, persistence, approvals and desktops are
  plain engineering. The one hard dependency is a model that can do pixel-level
  computer use. DeepSeek V4 can drive the shell, MCP and DOM-level browser children.
  Full-desktop control needs a vision model, which the fleet does not route today
  (see Q2).
- **Cost:** Grok Bot now ships with The X Subscription Premium at $30/mo. That is the
  cheapest tier, and it is enough to evaluate the product. **Recommendation: don't buy
  it for architecture work.** The gist already covers that. Buy one month of Premium
  only if someone will spend hands-on hours testing the learn-a-routine-from-one-demo
  UX, which is the only part the reconstruction can't show. No purchase was made: it
  needs Frank's card, which makes it a physical task, not a gate.
- **CCC takeaways:** (1) accept a prompt durably, return fast, stream results over SSE,
  with an idempotency nonce; (2) checkpoint every step so approvals and upgrades pause
  and resume; (3) a vault that lets agents *use* credentials without *seeing* them;
  (4) scheduled automations as just another producer into the same turn queue.

## Sources

| # | URL | What it gave |
|---|-----|--------------|
| S1 | https://x.ai/news/introducing-grok-bot (Aug 11 2026) | launch claims, multi-Bot, routines |
| S2 | https://x.ai/news/grok-bot-more-plans (Aug 26 2026) | plan expansion, job examples |
| S3 | https://www.buildfastwithai.com/blogs/grok-bot-review (Aug 13 2026) | third-party review, security caveat |
| S4 | https://gist.github.com/wtfsayo/b80f74f4f64d18ced8c84502a23148fe | **image-level reverse engineering** |
| S5 | https://docs.x.ai/grok-bot/overview | official docs: persistent computer, platforms, plans |
| S6 | https://docs.x.ai/grok-bot/vault.md | Vault credential model |
| S7 | https://cursor.com/help/grok-bot/plans | tier → access table, weekly pool |
| S8 | https://www.gosearch.ai/blog/grok-bot-pricing-and-alternatives/ (Oct 21 2026) | X Subscription prices |
| S9 | https://openrouter.ai/x-ai/grok-4.6 (search snippet) | Grok 4.6 API $2 in / $6 out per Mtok |

`https://x.ai/pricing` returned a Cloudflare block page to curl, so plan prices come
from S7/S8 and not from xAI's own pricing page.

## Q1. Architecture: what's the actual stack?

What xAI says publicly (S1, S2, S5): each Bot "works on a persistent cloud computer with
a browser, filesystem, and terminal." All Bots on an account share that one computer
(S1: "Bots share a computer of their own"). Bots message each other and can sit in
group chats. They "follow along" while you do a job and save it as a routine. Clients
exist for macOS, Windows, Linux, iOS and Android (S5).

What the reconstruction shows (S4). Their own labels separate exact source, recovered
source and inference. All of the following is recovered or exact source unless noted:

```
client ──HTTP POST /api/<cmd>──►  host-main.cjs (SandHost, :1340)
       ◄──GET /events (SSE)─────   gateway → transcript manager → per-agent runner
                                     ├─ inference RPC (Cursor-backed, short-lived token)
                                     ├─ Task children (browserUse / computerUse / general)
                                     ├─ approvals (per-surface review providers)
                                     └─ store.db + conversation-blobs.db (SQLite)
                                   │ ConnectRPC :1337 / PTY WS :1338 (bearer token)
                                   ▼
                                 exec-daemon: shell, files, PTY+tmux, MCP subprocesses,
                                              X11 computer use + screenshots
                                 sand-window-router :1339 → per-display exec daemons
                                 sand-supervisor → host, Xvfb, Chrome, x11vnc, noVNC :6081
PID1 /tini → pod-daemon (gRPC :26500, process creation from control plane)
```

Key mechanics:

- **Box = container per account.** `/tini` is PID 1, the disk is a 128 GiB ext4
  container filesystem with 66 overlay layers, and there is no kernel (S4 §"How the
  local recovery VM works"). Desktops are Xvfb displays `:1` (primary agent) and `:2–:4`
  (forked task desktops), each with its own Chrome profile. Port scheme for display N:
  exec 14000+N, PTY 13600+N, VNC 5900+N, CDP 9222+N.
- **Computer use comes in two tiers.** `browserUse` drives Chrome through CDP at the
  page/element level. `computerUse` works the whole X11 desktop with screenshots and
  input events. Each child gets a display allocated, protected by an owner token, and
  releases it when it settles. DOM-level is preferred because it is "faster and less
  disruptive." `sand-session-sync` mirrors cookies (including HttpOnly) and
  localStorage across Chrome instances over loopback CDP, which is how a login done in
  one window carries over to others.
- **Three kinds of agent.** (a) Named agents are durable: own transcript, profile,
  memory, schedules, permissions, reloaded at boot. (b) Task children are in-process
  `SandAgentRunner`s on the parent's box. They can't spawn their own children, they
  wake the parent on completion, and a watchdog catches stalls. (c) Cloud coding agents
  run on separate remote VMs with their own checkout and branch. The parent watches
  and imports the result.
- **Turn engine.** `sendPrompt` returns `{accepted:true}` once the prompt is durably
  enqueued, before any inference runs. A `clientNonce` plus `promptAcceptanceStatus`
  make retries idempotent. The turn streams model steps, and every tool call,
  approval pause and child dispatch writes a step checkpoint. A turn ends as success,
  awaiting-user, aborted, quiesced or error. Ownership generations stop a cancelled
  run from writing over a newer one.
- **Approvals.** Shell, box shell, MCP, computer use, automation writes, cloud agents
  and Task launch each have their own review provider. The outcome is allow, deny or
  pause-for-user, and a turn can have only one pending approval. An approval covers
  one exact action, not a category.
- **Persistence.** Each agent has `store.db` (KV plus transcript metadata) and a
  content-addressed `conversation-blobs.db`. The JSONL transcript and search index are
  *rebuildable projections*. Memory has three scopes (agent, shared-user, project),
  each with single-writer shards.
- **Automations** don't have a runtime of their own. Schedules, listeners and backend
  events feed a trigger hub, which enqueues ordinary turns tagged with
  trusted/untrusted provenance.
- **Inference** is "Cursor-backed." 36 `@anysphere/*` packages (Anysphere makes Cursor)
  show that the runtime is Cursor's agent stack. The host holds the model token and
  shell subprocesses never see it.
- **Security findings in the captured box:** an unauthenticated Docker API on :2375,
  an unidentified plaintext gRPC listener on :50052, and VNC with no password on
  loopback (S4 §"Security boundaries"). Isolation between displays is a routing token,
  not an OS user boundary. Every Bot on an account shares one trust domain, and Vault
  items can't be scoped to a single Bot (S6).

## Q2. Can we replicate it on the fleet with open models via LiteLLM?

Yes, except for one model-capability gap.

| Grok Bot component | Self-host equivalent | Difficulty |
|---|---|---|
| Box (container per account) | Docker/Podman or a systemd-nspawn slice on Isis/m80s; or a KVM VM for a real kernel boundary | low |
| Xvfb + window manager + Chrome + x11vnc + noVNC | stock Debian packages; one supervisor (systemd units or s6) | low |
| exec-daemon (shell/PTY/tmux/files/MCP) | an MCP host plus tmux; the fleet already runs MCP servers and the Commander MCP | low–med |
| browserUse child | Playwright/CDP; the fleet already has `mcp__playwright__*` and `mcp__stealth-browser__*` | low |
| computerUse child | screenshot → VLM → xdotool loop. Needs a grounding-capable vision model | **gated on model** |
| SandHost gateway + SSE + nonce idempotency | ~1–2k lines of Python/Node; Postgres or SQLite | med |
| Step checkpoints + approval pause/resume | med; this is the core engineering | med |
| Named agents + memory scopes | per-agent SQLite or Postgres rows; the fleet already has fleet-memory Postgres | med |
| Automations trigger hub | cron/systemd timers that enqueue turns; wq already does this at ticket granularity | low |
| Inference | LiteLLM gateway → `deepseek-v4-flash` (`research/models.yaml:234`) | done |

The model gap: `deepseek-v4-flash` is the fleet's route (`research/models.yaml:234`).
Its `deepseek-v4-flash-vision-exp` alias was folded into it
(`research/models.yaml:235,250`), and nothing in the repo shows that the route accepts
screenshots. DeepSeek V4 can drive every child except `computerUse`. For pixel-level
work we'd need a VLM trained for UI grounding. Candidates I know of are Qwen-VL-family
or UI-TARS-family models served locally behind LiteLLM (UNVERIFIED: names and fleet
GPU fit are from training knowledge; not searched on this ticket). The cheaper path is
the one Grok Bot itself prefers: DOM-level `browserUse` first, and computer use only
for native apps or hostile UIs.

Do **not** run the recovered image itself. The code is Anysphere/xAI proprietary,
inference needs their short-lived token, and the captured box ships the
unauthenticated Docker API noted above. Copy the patterns, not the binaries.

## Q3. Patterns vs the current fleet

| Concern | Grok Bot | Fleet today | Gap / novelty |
|---|---|---|---|
| Unit of work | durable named agent with an ongoing chat | wq ticket; one fworker session per ticket, then it exits | Grok keeps an *agent* alive across jobs; we keep a *ticket* alive across agents |
| Execution env | persistent container per account, shared by its Bots | fresh git worktree per ticket; `fworker-sandbox@.service` systemd slot with `ProtectHome=yes` (`ops/wq3652-m80s-provisioning/fworker-sandbox@.service:40`) | ours is more isolated (per ticket); theirs keeps state (cookies, shells, desktops) |
| Long jobs | supervisor plus checkpoint/resume across upgrades | `lumen-run` puts jobs into system `systemd-run` transient units so they survive gateway reaping (`ops/wq3606-lumen-run-log-mask/lumen-run:2-12`); watchdog; busy key | we survive process death but resume by re-dispatch, not from a step checkpoint |
| Parallelism | Task children in-process plus cloud coding agents on separate VMs | many fworkers in parallel, one per ticket | equivalent at the coarse grain; we lack in-session child wake-up |
| Coordination | Bots message each other; group chats | wq deps, mailbot, the critic/verify ticket chain | **novel: peer-to-peer agent threads with shared context** |
| Approvals | per-action pause with persisted request, resume at checkpoint | PreToolUse guard hook (deny/allow only); `wq block` for human decisions | **novel: pause-and-resume a turn rather than kill-and-redispatch** |
| Credentials | Vault: agent picks an entry by name and never sees the value (S6) | 0600 files plus git credential helpers; masking; cmd-secret-lint | same principle; theirs fills browser forms without exposing the secret |
| Learning | "follow along" demo → saved routine (S1) | skills, CLAUDE.md, ISSUE-LOG, proposal-only memory | **novel: record-a-demo → routine**; we write skills by hand |
| Client | text-thread app on mobile and desktop | terminal, shared tmux, wq CLI | CCC is meant to fill this gap |

## Q4. Is one month of paid access worth it?

Prices:
- The X Subscription (since 2026-10-12): Plus $8 (no Grok Bot), **Premium $30**, Super
  $100, Ultra $200 per month. Usage is a shared weekly pool across Grok, Grok Bot and
  Cursor, and higher tiers get 4×/15×/50× the Plus pool (S8). Cursor's help page
  confirms Premium/Super/Ultra include Grok Bot and Plus/Free don't (S7).
- Legacy SuperGrok and Cursor Pro/Pro+/Ultra/Teams plans also include it (S2, S7).
- Grok 4.6 API: $2.00/M input, $6.00/M output, $0.50/M cached read (S9). The ticket
  left this price blank.

Verdict: **not worth buying for reverse-engineering.** S4 already gives us more than a
month of poking around would, since it comes from the image itself, down to ports and
file layouts. The parts S4 can't show are (a) how good the demo-to-routine feature
really is and (b) how reliable Bots are on long jobs. For those, **Premium at $30 is
enough**: Ultra buys a bigger usage pool, not different features (S7, S8).
Imaging the box ourselves would add legal risk (proprietary code, ToS) and nothing the
gist doesn't already have.

If Frank wants hands-on UX evaluation anyway, file a physical task: "subscribe to X
Subscription Premium ($30/mo, cancel after 30 days)." Nothing else depends on it.

## Q5. Does this inform CCC?

Yes. These are the patterns worth adopting (see `research/wq2036_ccc_shared_state_design.md`
for CCC's current shared-state decision, per-tab OR-Set over per-host op-logs, which
these patterns don't conflict with):

1. **Accept fast, stream later.** CCC prompt submit returns once the prompt is durably
   stored. Everything else (thinking, tool calls, child status, approvals) arrives as
   typed SSE events. Use a `clientNonce` per logical prompt plus an acceptance-status
   query, so a flaky phone connection never runs a mutation twice.
2. **Step checkpoints with pause-for-approval.** Today a guard denial or a `wq block`
   kills the session, and it later costs a redispatch. Persisting the pending action
   and resuming at that boundary would remove whole redispatch cycles. This is the
   highest-value pattern here.
3. **Child agents wake the parent.** In-session children with a stall watchdog and a
   completion wake. Pair this with "children can't spawn children," which caps fan-out
   without a governor.
4. **Use-without-see credentials.** A vault broker that types a secret into a browser
   field or an HTTP header on the agent's behalf, where the agent only references the
   entry by name. That's a cleaner version of instincts 4 and 5 than masking after the
   fact. Do better than Grok Bot here: scope items per agent (S6 says Grok can't).
5. **Automations are just turn producers.** CCC schedules should enqueue ordinary
   turns with trusted/untrusted provenance, not run a separate cron runtime.
6. **Treat derived views as rebuildable.** Canonical state lives in the DB, and
   transcripts and search indexes are projections we can drop and rebuild.
7. **Avoid their mistakes:** one shared trust domain per account, unauthenticated
   Docker API, passwordless VNC. Bind CDP, VNC and daemon ports to loopback and give
   each agent its own box if credentials differ.

## Suggested follow-ups (not filed; for the board)

- BUILD: a CCC gateway spike with prompt accept, nonce, SSE event stream and step
  checkpoint table (pattern 1 and 2).
- RESEARCH: pick a self-hostable UI-grounding VLM and check GPU fit on Isis.
  Prerequisite for any `computerUse` child.
- PHYSICAL (optional, Frank): X Subscription Premium for 30 days, only if hands-on
  routine-UX evaluation is wanted.
