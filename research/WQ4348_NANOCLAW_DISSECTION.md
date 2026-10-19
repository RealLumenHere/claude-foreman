# wq#4348 — LIMPET RESEARCH: dissect NanoClaw

**What NanoClaw is:** a security-first, minimal alternative to **OpenClaw** (the
viral personal-AI-agent framework, launched Nov 2025 as "Clawdbot" before an
Anthropic trademark objection forced the rename). Built by brothers **Lazer and
Gavriel Cohen** (company **NanoCo**), launched February 2026, ~20–21k GitHub
stars and ~100k downloads within weeks. It wraps the **Claude Agent SDK**
(Claude Code's engine) in a thin orchestration layer — channel polling
(WhatsApp/Telegram/Discord/Slack/Gmail/web), scheduled tasks, per-group
persistent memory — and runs every agent invocation inside its own disposable
**Docker container** (or **Apple Container** on macOS). Pitch, in Cohen's own
words: *"treat agents as untrusted and potentially malicious... architecture
that assumes agents will misbehave and contains the damage when they do."*
On **2026-03-13** NanoCo partnered with Docker Inc. to run NanoClaw inside
**Docker Sandboxes** — disposable **MicroVMs**, one layer deeper than the
container boundary.

**Size claim, sourced:** OpenClaw is reported at **400k–500k LOC, 53 config
files, 70+ dependencies**, all security enforced at the application level
(allowlists, pairing codes) inside one shared Node process. NanoClaw is
reported at **under 4,000 LOC across ~15 core source files** — "up to 100x"
smaller (Cohen, via CloudNativeNow) — with security enforced by the OS
(container/VM boundary), not by the model behaving correctly.

**Sourcing note (instinct 8 — anti-fabrication):** every claim below came from
`fleet-search web` on 2026-10-19 (SearXNG→DeepSeek-Search fallback — SearXNG's
general engines were all suspended/CAPTCHA'd this run; the keyed DeepSeek
fallback carried the whole search), not memory — NanoClaw postdates the model
cutoff. **NanoClaw is a live, heavily-forked open-source project, not one
frozen artifact** — I pulled from the official docs site (`docs.nanoclaw.dev`),
the `docs/SECURITY.md` on **four different forks** (`nanocoai/nanoclaw` — the
org repo Cohen ships under, `gavrielc/nanoclaw`, `topcoder1/nanoclaw`,
`echthesia/nanoclaw`, `nickpourazima/nanoclaw`), a DeepWiki auto-doc of
`openclaw-shi/nanoclaw`, press coverage (Docker's own press release, ZDNet,
TechTarget, The Register, a Chinese line-number-cited source teardown), and one
**unmerged pull request** (`nanocoai/nanoclaw#1605`, by contributor
`ebenezer-isaac`) adding a sender/command gate. Where a feature shows up only
in a fork or an open PR rather than the canonical docs site, I've marked it as
such below — treat those as "exists in the ecosystem, not confirmed shipped on
`main`."

---

## Architecture diagram (container-per-agent + MicroVM, defense-in-depth)

```
  HOST PROCESS (trusted, single Node.js orchestrator)
  ┌───────────────────────────────────────────────────────────────────┐
  │ Channels (WhatsApp/Telegram/Discord/Slack/Gmail/web) ─▶ SQLite     │
  │   ─▶ per-group FIFO queue ─▶ container spawn decision              │
  │ Sender gate (owner_ids / trusted_members) — blocks before spawn    │
  │ Mount allowlist (~/.config/nanoclaw/mount-allowlist.json)          │
  │ OneCLI Agent Vault — holds the only real credentials               │
  └───────────────────────────────┬───────────────────────────────────┘
                                   │ docker run --rm --cap-drop=ALL
                                   │   --security-opt no-new-privileges
                                   │   --init --pids-limit <N>
                                   ▼
  ┌─ MicroVM (Docker Sandbox, own kernel + own Docker engine) ─────────┐
  │  ┌─ Container (one per agent GROUP, ephemeral per session) ─────┐ │
  │  │ uid 1000 (node), setuid bit stripped host-wide                │ │
  │  │ mounts: session folder (rw), group workspace (rw),            │ │
  │  │   container.json + composed CLAUDE.md (RO, nested on top      │ │
  │  │   of the rw group dir), shared agent-runner src (RO)          │ │
  │  │ NO project root, NO .ssh/.env/credentials, NO Docker socket   │ │
  │  │ network: --internal (egress lockdown) → OneCLI gateway only   │ │
  │  └─────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────┘
```

## 1. Container-per-agent (the primary boundary)

- **One container per agent *group*, fresh per session**, not shared across
  users/channels. Runs as unprivileged `node` (uid 1000); the image strips the
  setuid-root bit from every Debian binary that has one (`mount`, `su`,
  `passwd`) — none of which an agent needs. `--rm` on exit; a stale-heartbeat
  watchdog kills and rebuilds the container even mid-session, so a compromised
  process can't outlive its own session.
- **Nested read-only-over-read-write mounts**: `container.json` and the
  composed `CLAUDE.md` are mounted RO *on top of* the writable group
  directory — the agent can read its own config but cannot rewrite it to
  grant itself more. The **project root itself is never mounted** (current
  `nanocoai/main`) — host source (`src/`, `dist/`, `package.json`) is simply
  unreachable, closing the earlier "agent edits its own sandbox code" failure
  mode an older fork generation (`topcoder1`/`echthesia`/`nickpourazima`
  SECURITY.md snapshots) handled only by mounting that root read-only.
- **Symlink-trap guard on host writes into the container**: files flowing in
  (channel attachments, inter-agent forwards) are written by the *host* into
  the session's inbox. `src/inbox-safety.ts` verifies the inbox root and the
  per-message directory are real directories (not a compromised agent's
  symlink), resolves the final path, refuses anything landing outside the
  session folder, and uses exclusive-create flags so a pre-placed symlink
  can't be followed either — a concrete fix for a concrete host-write
  exfiltration bug class, not a hypothetical.
- **Blast radius is the agent *group*, not the session**: every session in a
  group shares that group's RW workspace and `memory/` tree, so a compromised
  session can read sibling sessions' transcripts in the same group
  (`ncl sessions history`, which does reject *cross-group* reads). Different
  trust domains therefore need separate groups, not just separate sessions —
  documented candidly as "one boundary the container does not draw."
- **Trust tiers baked into mounts, not prompts**: Main (trusted, self-chat)
  gets the project root read-only plus cross-group admin; non-main groups get
  only their own group folder, read-only global memory, and no project
  access at all — enforced by what's physically mounted, checked in code
  (`ncl` IPC authorization table), not by instructing the model to behave.

## 2. MicroVM-based Docker Sandboxes (the second isolation ring)

- Announced 2026-03-13 as a **NanoCo × Docker Inc. partnership**: NanoClaw
  becomes the first "claw"-style agent runtime deployable inside **Docker
  Sandboxes**, Docker's microVM-based execution primitive (shipped
  experimentally in Docker Desktop since Nov 2025). One command converts an
  install.
- **Two isolation layers, stacked**: *"Each agent runs in its own container
  (can't see other agents' data), and all containers run inside a micro VM
  (can't touch your host machine)"* (Cohen). A container is a process jail on
  a shared kernel; a microVM is a dedicated kernel and dedicated hardware
  space — Docker COO Mark Cavage's framing: *"agents break the container
  model"* because they want to mutate their own environment (install
  packages, spin up databases) at runtime, which containers were never built
  to tolerate safely; the microVM gives that mutation a bigger, cheaper-to-
  discard blast-radius boundary.
- **Platform support is currently partial and sourced-as-of-March-2026**:
  Docker Sandboxes shipped for **macOS (Apple Silicon) and Windows (x86)
  first, with Linux support called out explicitly as "coming in a few weeks"**
  in the launch coverage. Treat Linux-host microVM support as a thing to
  re-verify at adoption time, not a given.
- **Threat model it buys you**: if an agent achieves a container escape via a
  zero-day, the microVM still contains it — damage is capped at "a resettable
  microVM boundary," not the developer's laptop or a CI runner.

## 3. Hardening layers (all configurable, all defense-in-depth)

- **Egress lockdown** (`NANOCLAW_EGRESS_LOCKDOWN=true`, **off by default**):
  agent containers join a Docker `--internal` network (no internet route);
  the OneCLI gateway container is the *only* reachable hop, aliased as
  `host.docker.internal`; agents run non-root without `NET_ADMIN` so they
  can't reconfigure their way out. **Fail-closed by design**: if lockdown is
  requested but the gateway isn't up, NanoClaw throws `EgressLockdownError`
  and refuses to spawn rather than silently running with open egress. It's
  off by default specifically because it breaks any workflow needing non-
  proxy-aware egress — an explicit, documented trade-off, not an oversight.
- **Mount allowlist** (`~/.config/nanoclaw/mount-allowlist.json`, lives
  **outside the project root, never mounted into any container, not agent-
  editable**): per-group `additional_mounts` requests are checked in order —
  container path must be relative/non-empty/no `..`/no `:` (blocks Docker
  `-v` injection) → host path is tilde-expanded and `realpath`-resolved
  *before* checking (defeats symlink traversal) → rejected if it matches a
  blocked pattern (`.ssh`, `.gnupg`, `.aws`, `.kube`, `.docker`,
  `credentials`, `.env`, `.netrc`, `id_rsa`/`id_ed25519`, plus — notably —
  `.config/nanoclaw` and `.local/bin` themselves, so a container can't name
  NanoClaw's *own* security-config directory or host-executed helper
  binaries) → must sit under an `allowedRoots` entry → **forced read-only
  unless the mount explicitly requests RW *and* its root allows RW**. **No
  allowlist file at all ⇒ every additional mount is blocked** — default-deny,
  opt-in posture.
- **Sender policy / command gate** — the ticket's framing matches
  `docs.nanoclaw.dev/operate/hardening` almost verbatim, but the concrete
  implementation is **an open PR (`nanocoai/nanoclaw#1605`, not confirmed
  merged)**: a deterministic `owner_ids`/`trusted_members` sender gate that
  blocks unknown senders *in code, before a container is even spawned* (no
  tokens burned, "zero chance of the bot deciding to respond anyway" —
  replacing the old approach of prompting the model with "ignore strangers"
  in `SOUL.md`, which is unreliable by construction), plus a `canUseTool`
  callback inside the container that regex-blocks dangerous Bash commands,
  blocks `$VAR`/`${VAR}` references to configured secret env-var names, blocks
  URL patterns, and **fails closed (deny-all) if the security-rules object is
  missing**. The same PR adds a read-only-mounted, agent-unresettable file
  **killswitch** checked before every spawn and every message pipe. A
  *separate* fork lineage (`.claude/skills/edit-policy` in `nanocoai/main`)
  shows a first-match-wins **YAML policy engine** (`agentsh`) with its own
  container vs. host policy files and `file_rules`/`network_rules`/
  `command_rules`/`resource_limits` categories — this may be a newer/parallel
  implementation of the same idea rather than the PR #1605 code; I could not
  confirm from search results alone which one (if either) is what currently
  ships on `main`.
- **Credential isolation (OneCLI Agent Vault)** — real API credentials
  **never enter any container**. Credentials are registered once
  (`onecli secrets create`); outbound HTTPS is routed through the OneCLI
  gateway, which matches by host+path and injects the real credential in
  transit; a container asking a tool for a credential file gets a stub whose
  value is the literal placeholder `onecli-managed`. A compromised agent can
  dump env/filesystem/`/proc` and find nothing exfiltratable — it can *use*
  credentials its group was granted (gateway-injected per request) but can
  never carry them out. **If the vault is unreachable, spawn aborts rather
  than falling back to raw keys.** Caveat worth keeping: at least two older
  fork snapshots (`echthesia`, `nickpourazima`) admit the *Anthropic/Claude
  Code auth token itself* (`ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`) is
  still passed into the container as a real env var so Claude Code can
  authenticate — with the maintainer's own comment, *"Ideally Claude Code
  would authenticate without exposing credentials... PRs welcome."* The
  vault pattern may not (yet) cover the one credential the agent's own
  harness needs to run at all.
- **Container resource limits** — **opt-in, unset by default**:
  `CONTAINER_CPU_LIMIT`/`CONTAINER_MEMORY_LIMIT` map directly to Docker's
  `--cpus`/`--memory` when set; `--memory` is a hard cap only if the host has
  no swap (an operator deployment concern, not something NanoClaw enforces).
  `CONTAINER_PIDS_LIMIT` (default **2048**) is a fork-bomb backstop — set well
  above normal usage because thread-heavy processes (the docs call out
  Chromium specifically, for browser-automation skills) need headroom. An
  earlier-generation config surface (DeepWiki snapshot of `gavrielc/nanoclaw`)
  also shows a 30-minute `CONTAINER_TIMEOUT`, a 10 MB `CONTAINER_MAX_OUTPUT_SIZE`
  truncation cap, and a global `MAX_CONCURRENT_CONTAINERS` (default 5)
  queued via a `GroupQueue` — whether those specific constants persist
  unchanged on current `main` wasn't independently confirmed this search.

## 4. Auditable small codebase

- **Under 4,000 LOC, ~15 core source files, "fewer than 10 dependencies"**
  (ticket framing, corroborated by the "100x smaller than OpenClaw" press
  figures) — small enough, per Cohen, that *"a competent developer can review
  the entire codebase in an afternoon."*
- **This is an enforced project policy, not an incidental side effect.**
  NanoClaw's own contribution guidelines (per the Dutch-language blog post
  translation) accept **only bugfixes, security fixes, and simplifications**
  into core. New functionality ships as **skills** — a written instruction
  plus a full reference implementation that a coding agent (Claude Code
  itself) integrates into *your fork* — explicitly to keep the reviewable
  core from growing. The project's own description: *"Not a monolithic
  framework... you make your own fork and have Claude Code modify it to match
  your needs."* This is the mechanism that keeps the 4,000-line claim durable
  release over release, rather than a one-time snapshot that will drift the
  moment people start contributing features.
- Caveat: "15 core files" and "<4,000 LOC" describe the orchestrator/host
  process. It still depends on the Claude Agent SDK itself (not counted) and
  ships a container image (Node 22 + Chromium for browser skills) whose own
  size isn't part of the LOC claim.

---

## Feature-extraction list (what NanoClaw actually ships)

1. **Container-per-agent-group**, ephemeral (`--rm`, stale-heartbeat rebuild),
   non-root, setuid-stripped image, `--cap-drop=ALL`,
   `--security-opt no-new-privileges`, `--init`, configurable `--pids-limit`.
2. **Nested RO-over-RW mounts** for the agent's own config (`container.json`,
   composed `CLAUDE.md`) — readable, not self-modifiable.
3. **Project root never mounted** into the container (current `main`); no
   path by which the agent can rewrite host application code.
4. **Symlink-trap-safe host→container file writes** (`inbox-safety.ts`):
   real-directory checks + exclusive-create, specifically against a
   compromised agent redirecting host writes via a planted symlink.
5. **Second isolation ring via Docker Sandboxes (MicroVM)** — dedicated
   kernel per sandbox, contains container-escape zero-days; opt-in, platform-
   partial (macOS/Windows first, Linux pending as of the March 2026 launch).
6. **Egress lockdown**: `--internal` Docker network + single-gateway-hop
   topology, fail-closed on misconfiguration, off by default.
7. **External, non-agent-writable, default-deny mount allowlist** with
   symlink-resolved validation and a blocklist that explicitly protects its
   own config directory and host-helper-binary directory.
8. **OneCLI Agent Vault**: credential injection at the network edge; no real
   secret ever placed in a container filesystem, env, or `/proc` (with the
   one documented exception of the harness's own Anthropic auth token on at
   least some fork snapshots).
9. **Deterministic sender/command gate** (open PR): code-level allow/deny
   before spawn and inside the container, replacing prompt-based "ignore
   strangers" instructions; fails closed if rules are missing; file-based
   killswitch the agent can't re-enable because it's read-only mounted.
10. **Opt-in resource ceilings**: CPU/memory caps, a fork-bomb `pids-limit`
    backstop, execution timeout, output-size truncation, global concurrency
    queue.
11. **A codebase-size ceiling enforced by contribution policy**, not just a
    marketing number — new capability is pushed into user-maintained forks
    via "skills" rather than into the reviewed core.

## Recommendations for Limpet

Limpet already has one piece of this architecture: the **build sandbox has no
egress** (`limpet/DESIGN-DECISIONS.md` D3, wq#2595/wq#3997) — fetches are
fail-closed by design, downloaded exploit bodies are explicitly kept
untrusted and never auto-run. NanoClaw validates that posture and gives
Limpet a template for the piece it doesn't have yet: **a reusable, ephemeral,
per-task execution boundary for running something actually untrusted**
(a target binary, extracted firmware, a QEMU guest) — which is exactly the
RE-agent use case this ticket calls out.

- **A.** **Container-per-RE-task, ephemeral by construction.** When an RE
  agent (Ghidra/firmware-emulation/binary-execution skills) needs to run
  untrusted code, spawn it in its own throwaway container —
  `--rm`, non-root, `--cap-drop=ALL`, `--security-opt no-new-privileges`,
  `--init` — rather than running it in the agent's own worktree/shell. This is
  the single highest-value primitive NanoClaw proves: a small, auditable
  wrapper around `docker run` is enough to turn "the agent can execute
  arbitrary untrusted code" from a host-wide risk into a boundary that resets
  every invocation.
- **B.** **Mount allowlist, not ambient access.** Mirror
  `mount-allowlist.json`'s shape for any RE-task container: an allowlist file
  that lives *outside* whatever the task mounts, is never itself mounted in,
  defaults to deny-all when absent, resolves symlinks before checking, and
  hardcodes a blocklist (`.ssh`, `.env`, credential files, and Limpet's own
  config/creds directories) that the per-task config can extend but not
  remove. Only the specific firmware dump / extracted filesystem / target
  binary directory should ever be visible inside, and read-only unless the
  task genuinely needs to write (e.g. a patched re-flash image).
- **C.** **Egress lockdown should be the RE-container's default, not an
  opt-in.** Limpet's build sandbox already has no egress; extend the same
  fail-closed stance (refuse to spawn rather than silently allow open
  network) to any ad hoc container built for firmware emulation or binary
  execution — a malicious or compromised firmware sample trying to phone home
  is exactly the exfiltration path egress lockdown closes.
- **D.** **A fork-bomb/resource backstop, defaulted ON (unlike NanoClaw's
  opt-in default).** Firmware emulation (QEMU) and arbitrary binary execution
  are precisely the workloads that runaway-process/fork-bomb themselves; set
  `--pids-limit`, `--cpus`, `--memory` by default for RE-task containers
  rather than leaving them unbounded until an operator remembers to configure
  them — NanoClaw's own docs flag this as an opt-in gap, not a strength to
  copy verbatim.
- **E.** **Enforce in code, not in the prompt.** The PR #1605 rationale —
  replacing "tell the model to ignore strangers" with a deterministic gate
  the agent never even sees past — is the same principle this fleet already
  runs on (the `guard.sh` PreToolUse deny-list, instinct 4's mask-before-print).
  If/when Limpet adds a "don't run this category of command inside the RE
  container" rule, write it as a code-level gate on the spawn/exec path, not
  as agent instructions the untrusted-code-adjacent agent could talk itself
  out of.

## Anti-patterns to avoid

1. **Application-level-only enforcement** (OpenClaw's default mode: one
   shared Node process, allowlists and pairing codes as the *entire* security
   model) — this is the failure mode NanoClaw's whole architecture exists to
   fix. Don't let an RE-task boundary be "the agent promises not to," ever.
2. **Sharing one container/session across trust domains.** Even NanoClaw's
   own docs admit the blast radius is the *group*, not the session, because
   sessions in a group share a writable workspace — if Limpet ever runs
   multiple concurrent RE tasks against different untrusted samples, they
   need separate containers, not separate sessions in one.
3. **Assuming "no secrets in the container" is automatically true.** At least
   two NanoClaw fork snapshots still pass the harness's own API token into
   the container as a real env var, with the maintainer admitting it's an
   open problem. If Limpet's RE-agent harness needs its own credential to
   run, name that gap explicitly rather than assuming container isolation
   solved it by default.
4. **Resource limits that are opt-in by default** on a workload that's
   expected to run untrusted/adversarial code — NanoClaw ships this way
   because its typical workload (chat agents) rarely fork-bombs; Limpet's RE
   workload (QEMU, fuzzing, arbitrary binaries) is exactly the case where
   unbounded-by-default is the wrong direction to fail in.
5. **Trusting a feature because it's in a popular fork.** NanoClaw's own
   ecosystem is forked widely enough (at least 7 distinct
   `docs/SECURITY.md`/config variants turned up in one search) that "NanoClaw
   does X" needs a repo+commit, not just the project name — carry that
   discipline into any Limpet writeup that cites it later.

---

**Bottom line for Limpet:** NanoClaw's core claim — that container-per-task
plus a non-agent-writable, default-deny mount/egress boundary is cheap enough
to fit in ~4,000 auditable lines — is directly applicable to the RE-agent
problem this ticket names. Limpet already has half of this (the no-egress
build sandbox); the gap is a reusable, ephemeral, resource-capped,
allowlisted container primitive for the other half: actually running
something untrusted (A–D). The one thing to *not* import uncritically is
NanoClaw's "opt-in" defaults (egress lockdown, resource limits) — those are
reasonable for a chat-agent workload and wrong for one built to execute
untrusted binaries and firmware on purpose.
