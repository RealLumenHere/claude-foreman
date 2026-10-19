# wq#4336 — LIMPET DISSECT: xAI/SpaceXSI Grok Bot, the always-on-agent product peer

Status: research/design. Author: build worker, 2026-10-19. Dissects Grok Bot
(launched 2026-08-11) across the six axes the ticket names, extracts features, and turns
the adoptable patterns into concrete Limpet artifacts. Companion to the agent-product
reads in `design/wq4059_sentinel_agent_eval.md` and the orchestration evals
(`docs(research): wq4294-wq4297`).

## 0. Identity, license, grounding

- Product `Grok Bot` by SpaceXSI (ex-xAI), `@bot`. Launched 2026-08-11 as early beta.
  **Closed/proprietary SaaS** — not adoptable as code; this is a *pattern* dissection, not
  a vendor/wrap candidate. Billing rides Cursor + SuperGrok plans; its own usage pool.
- Clients: macOS (Apple silicon + Intel), Windows (x64/Arm64), Linux (.deb/.rpm/AppImage),
  iOS 18+, Android 9+. The agent itself runs on a **cloud computer**, not the client.
- **Grounding (live, not memory):** every quoted claim below was fetched 2026-10-19 via
  `fleet-search read` against the canonical pages, because this product postdates useful
  model memory and the ticket's own framing contains one factual error (see §1 note):
  - `https://x.ai/news/introducing-grok-bot` (launch post)
  - `https://x.ai/bot`, `https://x.ai/bot/use-cases` (product + demos)
  - `https://docs.x.ai/grok-bot/approvals-security-and-privacy` (the approval gate)
  - `https://docs.x.ai/grok-bot/faq`, `/troubleshooting` (compute, multi-agent, recovery)
  - `https://docs.x.ai/grok/connectors` (tool/auth model; last updated 2026-10-10)

Grok Bot's own one-line framing, verbatim from the launch post:

> "Bots have their own computer. They sign into the tools you already use and work across
> apps, inboxes, and more. They finish jobs end to end, and only come back when something
> needs your approval."

---

## 1. Dedicated compute model

**What the marketing says vs. what the docs say — and the ticket inherits the marketing
error.** The launch post and the ticket both say "each agent gets its own computer." The
security and FAQ docs explicitly correct this:

> "All of your Bots share one cloud computer assigned to your user account. Files, browser
> sessions, and command line credentials on that computer are available across your Bot
> roster." — approvals-security-and-privacy
>
> "Every Bot on your account uses one persistent cloud computer... The computer is assigned
> per user, not per Bot. Do not use separate Bots as a security boundary." — FAQ

**Architecture (reconstructed):**
- **One persistent, long-lived cloud VM per *user account*** (not per task, not per Bot). It
  is a full desktop OS image: browser, command line, a `/workspace` filesystem, and
  connected tools (`docs .../computer-and-apps`). "Persistent" and image-`Update`/`Reset`
  semantics (§5) say **VM with a mutable disk image + snapshots**, not an ephemeral
  per-task container.
- **Each Bot gets its own *screen* on that shared VM**, and "one Bot can run one
  computer-use task on its screen at a time" (FAQ). So the concurrency unit is a screen/
  session multiplexed onto one machine — closer to multi-seat on one box than to
  per-agent isolation.
- **State persistence between tasks** is the VM's durable disk: files in `/workspace`,
  browser sessions, and CLI credentials survive across tasks and across Bots. Memory is
  layered on top: each Bot keeps "stable preferences, role context, and summaries of prior
  work," and its conversation + learned role are private, while **files, browser sessions,
  group messages, and direct handoffs are the shared channel** that moves context between
  Bots (FAQ).

**Diff vs Limpet.** Limpet's compute story is the inverse and, for the RE domain, stronger:
Limpet runs on fleet nodes / the Limpet ISO with a HAL that physically attaches to target
devices (`docs/hal-spec.md`), and its isolation boundary is a real one (worktrees, per-lane
bounds). Grok Bot's "shared computer, explicitly not a security boundary" is a deliberate
*collaboration* choice (shared creds so Bots hand off cheaply) that Limpet must **not**
copy wholesale: when agents share a device plane AND credentials, the energize-confirm gate
and the Meta Sentinel attestation (§2) carry the entire safety load. The one adoptable
idea: a **persistent per-operator workspace** with durable artifacts that survives across
RE tasks, so a dump analyzed Monday is still staged Friday.

## 2. Approval gate — classification, and the Limpet / Meta Sentinel comparison

**How Grok Bot decides autonomous vs. needs-approval** — three layers, none of them a hard
code/data taxonomy:
1. **Natural-language boundaries in the request / Bot description.** The operator writes the
   stop line in prose: *"Reconcile the campaign data and draft a recommended budget change.
   Do not change the campaign or message the agency. Ask for approval after showing the
   current value, proposed value, and expected impact."* Recommended boundary classes:
   sending messages/invitations, publishing, purchases/financial transfers, delete/
   overwrite, permission changes, production changes, accepting legal terms.
2. **Auto Review** — "Grok Bot evaluates tool calls and computer actions before they run."
   Operator-authored rules: *Ask first* rules always stop a matching action; *Always allow*
   saves a matching rule that auto-approves. Per-card controls: **Allow once / Always allow
   / Deny**.
3. **A risk model.** "The answer depends on the tool, the risk of the action, and—when
   enforcement is enabled—your Auto-review rules." Human-only steps (passwords, 2FA,
   CAPTCHAs) are never classified-through — they force a **computer takeover** by the human.

Two load-bearing details:
- **Background approvals fail closed on a timeout.** An approval from an interactive chat
  waits for the answer. An approval from work that started without you — a routine, a
  trigger, a message from another Bot — **expires after ~10 minutes**; the card shows
  *Expired* and **the action does not run**. The expired card can offer "Always allow this
  in the future" (captures the rule without running the expired action).
- **The gate is prospective, never a rollback.** Verbatim: *"An approval controls the
  proposed action. It does not reverse work already completed."*

**Compare to Limpet's energize-confirm gate (`docs/hal-spec.md:682-734`).** Limpet's gate is
*harder* and *narrower*, correctly for its domain:

| Dimension | Grok Bot Auto Review | Limpet energize-confirm |
|---|---|---|
| Classification | risk model + NL boundaries + user rules (soft, learnable) | binary `read_only` vs `energizing`, declared in the plugin manifest (hard) |
| Override | `Always allow` can retire a prompt permanently | **cannot** be overridden by config, plugin settings, or prompt (`hal-spec.md:698-700`) |
| Missing/ambiguous | risk model's judgment | **fail closed → `energizing`** (`hal-spec.md:733-734`) |
| Scope | all consequential actions | actions that *energize hardware* (flash write, JTAG/SWD, GPIO, bootloader unlock, exploit-db fire) |
| Timeout | background approval expires ~10 min, fails closed | n/a (per-call confirm token) |

Grok Bot validates two Limpet design choices and offers two patterns to steal:
- **Validates** that the gate must be *pre-action* (both say post-hoc approval cannot undo a
  side-effect) — this is exactly why Limpet confirms *before* energizing, since a flashed
  device cannot be un-flashed. And validates fail-closed defaults.
- **Steal (P1):** the **~10-minute expiry on unattended approvals**. Limpet's autonomous
  lanes should treat an energize-confirm that nobody answers within a bounded window as a
  hard refusal, not a hang — a time-boxed fail-closed, logged.
- **Steal (P2):** **"Always allow this in the future" rule capture** — but *scoped and
  reviewable*, never for `energizing` actions. A read-only probe an operator approves twice
  could propose a persisted allow-rule (routed to review like memory candidates, instinct 7),
  while energizing actions stay confirm-every-time regardless.

**Compare to Meta Sentinel** (`design/wq4226_limpet_conformance_suite.md:194-198`,
`sentinel/`). Limpet's meta-server consults a **device-side sentinel** before any energizing
`tools/call`, and conformance test G1 proves a *gateless* host must **FAIL** — the device
records zero energize ops when the sentinel is not consulted. Grok Bot's Auto Review is the
*host-side* analog (evaluate tool calls before they run) but has **no device-side
attestation** that the gate actually ran: the enforcement lives in the same cloud VM that
executes the action. Limpet's split — enforcement attested on the far side of the HAL — is
strictly stronger, and the shared-computer model (§1) is exactly why: when all agents share
credentials, only an out-of-band attestation can prove the gate fired.

## 3. Tool integration — auth model and extensibility

**Auth foundation:** Grok Bot rides **Cursor authentication + Cursor account data settings**
(it cannot run in Cursor's "Legacy Privacy Mode" because it requires cloud storage). Tools
attach in three tiers (`docs/grok/connectors`, last updated 2026-10-10):

1. **Built-in connectors** — SpaceXSI-maintained, native, **OAuth once**: Gmail/Google
   Calendar, Google Drive, OneDrive, Outlook Mail/Calendar, Microsoft Teams, SharePoint,
   Salesforce (read + create/update).
2. **Catalog connectors** — pre-configured OAuth, no setup beyond sign-in: Box, Canva,
   Gamma, GitHub, Linear, Meltwater, Notion, S&P Global, Vercel.
3. **Custom MCP connectors** — **bring-your-own MCP server**: expose any internal API/DB/
   SaaS, define your own tool schemas, control auth on your own infra. Added at
   `grok.com/connectors → New Connector → Custom → MCP URL + auth`; Grok discovers the
   tools the server exposes. Must be reachable over the public internet (tunnel if local).

**Fallback plane:** for any site without a connector, the Bot uses **computer use** — it
signs in and clicks like a human, "including platforms with no clean API or MCP." Login,
CAPTCHA, 2FA are handed back via computer takeover; the Bot must not bypass them. Hardware
security keys (YubiKey) on the operator's desktop are supported via a browser passthrough
(macOS/Windows; not Linux), approved every use.

**How new tools are added:** OAuth sign-in for tiers 1–2; a custom MCP URL for tier 3; or
nothing at all — just point the Bot at a website.

**Diff vs Limpet / patterns.** This three-tier model maps almost one-to-one onto Limpet's
existing surfaces and should be adopted as the *mental model* for the plugin registry
(`feat(limpet): plugin registry — federated git-index + content-bound trust`, c193e5bd):
- Tier 1 "built-in" ↔ Limpet **core plugins** (HAL-bundled, highest trust).
- Tier 2 "catalog" ↔ Limpet's **federated git-index registry** with content-bound trust —
  same "curated, no-setup, trust-the-maintainer" tier.
- Tier 3 "custom MCP" ↔ Limpet's **meta-server MCP surface** / BYO plugin.
- **Steal (P1):** Grok's "connector when available, else computer-use" degradation is a good
  default, but for Limpet every computer-use/browser step over an RE target must inherit the
  energize-confirm classification — a browser action that writes to a device web-UI is
  `energizing`. Grok's REx@Skill-style injection risk here is real; Limpet already has the
  quarantine gate (`feat(limpet): REx@Skill prompt-injection quarantine`, 210e61a1) and must
  apply it to connector/computer-use output.

## 4. Multi-agent — coordination

Yes, multiple Bots per account run in parallel. Patterns, verbatim-grounded:
- **Chief-of-staff + specialists.** "One to manage the others. A chief of staff sits on top,
  with a specialist for each lane: inbox management, expenses, recruiting, bug fixes, or
  operations." The human stops being the middleman.
- **Direct Bot-to-Bot messaging.** "Bots can independently message each other and share
  context in threads... stay aligned on the same account or project without requiring you to
  paste notes between chats."
- **Group chat.** "Place Bots in a group chat where they can coordinate on their own. They
  pass work, assign ownership, and only pull you in for judgment calls."
- **Shared-workspace coordination.** Files, browser sessions, logins, group messages, and
  direct handoffs are the shared substrate (§1). Each Bot keeps a private conversation +
  learned role.

**Diff vs Limpet / patterns.** The chief-of-staff topology is directly what Limpet's
fleet already does (fworker dispatch → BUILD lanes). The adoptable RE mapping:
- **Steal (P1): a chief-of-staff orchestrator over RE lane-specialists** — an extraction
  Bot → static-analysis Bot → emulation Bot → exploit Bot, passing artifacts through a
  shared workspace, with the orchestrator only escalating energize decisions to the human.
  This is the multi-agent shape Limpet's conformance work already assumes.
- **Reject:** Grok's "do not use separate Bots as a security boundary / shared credentials"
  model. Limpet's lanes are worktree-isolated and the energize-confirm gate is per-call;
  collapsing that to a shared cred plane would defeat the sentinel attestation (§2). Adopt
  the *coordination* (handoff, ownership, escalation), not the *shared-trust* substrate.

## 5. Error recovery

Grok Bot's recovery is a **graduated, least-destructive-first ladder** with no automatic
rollback of external side-effects (`docs .../troubleshooting`):

- **Compute-level ladder:** `Retry` from the error state → `Restart` the app → `Recover
  computer` (preserves durable files + logins) → `Update Bot's Computer` → **`Reset`**
  (restores the last saved snapshot; **can lose recent/unsynced work**). "Start with the
  least destructive step." Background turns keep running when the client is closed.
- **Snapshots:** `Reset` restores the last saved snapshot; `Recover`/`Update` preserve
  durable files. So the compute plane has rollback, but only of *the machine*, not the world.
- **Task-level "stuck":** check sidebar/conversation status, open the computer to see if the
  Bot waits on a page/approval/login/CAPTCHA, send a short redirect, or send "Stop now."
  One active computer-use task per Bot screen must finish or be redirected first.
- **Connector failure:** reconnect/re-auth flow; if auth was revoked at the source, remove
  and reconnect.
- **Notification:** approval cards, *Expired* cards, stuck status in the sidebar, and the
  Bot "knowing when something needs your attention." No retry storm — failures surface as a
  state the human (or orchestrator) acts on.
- **No side-effect rollback.** Consistent with §2: completed external actions are not undone.
  Recovery is **forward** (retry/redirect) or **snapshot-restore of the VM**, never a
  transactional undo of a send/publish/write.

**Diff vs Limpet / patterns.**
- **Steal (P1): the least-destructive-first recovery ladder** as explicit Limpet states —
  `Retry → Restart → Recover(keep artifacts) → Reset(snapshot, lossy)` — for HAL/daemon
  recovery, so a wedged session has a named, bounded escalation instead of ad-hoc restarts.
- **Validates:** "no rollback of external side-effects" is the honest counterpart to
  energize-confirm — because a flashed/bricked device cannot be snapshot-restored, Limpet's
  only safe lever is the *pre-action* gate, exactly as designed. The RE domain is strictly
  less reversible than Grok's SaaS domain, so Limpet must be strictly more conservative.

## 6. Use-case demos and the RE-applicable workflow patterns

Demo lanes (from `x.ai/bot/use-cases`, `x.ai/news/...`): **sales** (overnight account
research, intent-scored contacts, voice-matched email/LinkedIn drafts, CRM updates from call
transcripts), **support/CS** (pre-QBR risk+expansion watch lists from usage signals),
**recruiting** (screen-to-offer: source, draft outreach, skip ATS dupes, schedule after
approval; score applications vs a defined bar → ATS-ready review), **eng** (reproduce a bug
by clicking the same path in staging, capture the failure, drop a *repro pack* of steps +
screenshots + network notes, file the ticket, hand the fix to a debugging Bot; perf: log
into observability, walk flamegraphs, return hotspots + screenshot writeup), **ops** (weekly
expense summary, log receipts from email, nudge owners; seat new hires; process invoices).

**Four workflow patterns that transfer to RE:**
1. **Repro-pack (eng bug-repro) → RE evidence pack.** "Click the same path in staging,
   capture the failure, drop a repro pack (steps, screenshots, network notes)" is *exactly*
   an RE triage artifact: walk the device-enumeration path, capture VID:PID/dumps/bus
   traces, emit a structured evidence pack. This is the same shape as Limpet's "done means
   demonstrated, evidence cited" doctrine — adopt the repro-pack as the standard RE output
   artifact (steps + screenshots + bus/network capture + file:line citations).
2. **Research-overnight → review-list-at-morning (async, review-gated).** Do the long
   unattended work (overnight extraction + static analysis), leave a ranked findings list,
   human approves. Pairs with the §2 expiry: the review list is where energize decisions
   queue. Adopt for long firmware passes.
3. **Observability-walk → firmware-walk.** "Log into observability tools, walk the
   flamegraphs, come back with hotspots + a short writeup with screenshots" ↔ walk the
   firmware/binary, return annotated hotspots (crypto routines, auth logic, injection sinks)
   with a short writeup. Maps onto the `ghidra-re` / `firmware-static-analysis` skills.
4. **Teach-a-task → routine.** Record one browser workflow (≤10-min cap), the Bot drafts a
   skill, operator reviews/tests, then it runs on a schedule or trigger. Adopt: let an
   operator demonstrate an RE workflow once and have Limpet save a reviewable draft skill —
   with the hard rule that any `energizing` step in the recorded routine keeps its
   confirm-every-time gate and is never auto-fired by the saved routine.

A cross-cutting observation: a large share of Grok's own demos are explicitly **"review-only
so it never sends on its own," "start with read-only tasks and draft outputs,"
"least-privilege."** That is independent convergence on Limpet's read-only-autonomous /
energize-confirm posture from a vendor with far lower stakes than hardware — strong external
validation that the gate is the right default, not an over-constraint.

## 7. Extraction summary — what Limpet should take

| # | Pattern | Source axis | Limpet target | Prio |
|---|---|---|---|---|
| 1 | Time-boxed fail-closed on unanswered energize-confirm (~10 min → refuse+log) | §2 | HAL daemon / meta-server | P1 |
| 2 | Scoped, reviewable "always allow" for **read-only** actions only | §2 | energize-confirm + memory-candidate review | P2 |
| 3 | Three-tier tool model: core / federated-catalog / BYO-MCP as the registry mental model | §3 | plugin registry (c193e5bd) | P1 |
| 4 | Connector-else-computer-use degradation, with injection quarantine on all of it | §3 | meta-server + REx@Skill gate (210e61a1) | P2 |
| 5 | Chief-of-staff orchestrator over RE lane-specialists, coordination without shared creds | §4 | fleet dispatch / conformance | P1 |
| 6 | Least-destructive-first recovery ladder as named states | §5 | HAL/daemon recovery | P1 |
| 7 | Repro-pack as the standard RE evidence artifact | §6 | output doctrine / skills | P1 |
| 8 | Teach-a-task → reviewable draft skill (energizing steps stay gated) | §6 | skill authoring | P2 |

**Explicitly rejected:** the shared-computer / shared-credential model as a security posture
(§1, §4) — Limpet keeps per-lane isolation + device-side sentinel attestation, which the
RE domain's irreversibility demands and Grok's SaaS domain does not.
