# wq#4335 — LIMPET RESEARCH: dissect Meta Muse

**What Muse is:** a consumer *execution* agent (not a chatbot) Meta launched
**2026-09-08** — "the first personal AI agent for everyone." The user states a goal;
Muse opens a browser, fills forms, sends email, negotiates, and pays on the user's
behalf, and keeps running after the app is closed, returning only for sensitive
actions. Model is Meta's own (marketed "Muse Spark"). ~600k US DAU / 2.5M downloads
in the first weeks (Sensor Tower via Bloomberg/Reuters). Its file layout
(`SOUL.md`, `memory/`, `tools/`) is highly cognate with the open framework
**OpenClaw** — that lineage is where its security boundary comes from.

**Sourcing note (instinct 8 — anti-fabrication):** every architectural claim below
was pulled from web search on 2026-10-19 (SearXNG/DeepSeek fallback), not from
memory — Muse postdates the model cutoff. Primary sources seen: a Yahoo/CNBC launch
writeup quoting Meta VP David Singleton and safety lead Wang; a GrokBot-vs-Muse VM
comparison (Pondero); a Patrick Wardle 0-day report; Reuters/TechCrunch/Ars on the
Full-Disk-Access dispute; a long Chinese teardown enumerating the six-week incident
set; the OpenClaw Enterprise (OCE) blog. **The "40% incident spike" figure is the
ticket's own framing** — I confirmed the *pattern* (≈7 distinct incidents in six
weeks) but did **not** independently source the exact 40%; treat the number as
ticket-provided, the incident taxonomy below as sourced.

---

## Architecture diagram

```
   ┌────────────────────────── USER'S PHONE / MAC APP ──────────────────────────┐
   │  Thin client: goal input, approval prompts, result view. Holds no agent.    │
   └───────────────▲───────────────────────────────────────────┬────────────────┘
       approval req │ (push / in-app banner)       goal, grants │
   ════════════════╪═══════════════════════════════════════════╪═══════ Meta cloud
   ┌───────────────┴───────────────────────────────────────────▼────────────────┐
   │  PER-USER SECURE VM  (dedicated Linux VM, one per user — NOT per account)    │
   │                                                                              │
   │   ┌──────────── systemd-nspawn container (the agent's blast radius) ──────┐  │
   │   │  Muse agent (OpenClaw-derived loop)                                   │  │
   │   │    SOUL.md · memory/ · tools/ · browser · filesystem · terminal       │  │
   │   │    holds ONLY opaque surrogate tokens — never real creds              │  │
   │   └──────▲───────────────────────────────────────────────┬───────────────┘  │
   │          │ every egress + sensitive action               │ API call w/      │
   │          │ (fail-CLOSED: no Sentinel verdict = blocked)   │ surrogate token  │
   │   ┌──────┴───────────────────────────────────────────────▼───────────────┐  │
   │   │  SENTINEL  (host-side gate, OUTSIDE the container)                     │  │
   │   │    classify action → {proceed | block | ask-user}                     │  │
   │   │    scope check: connector? action-kind? granted scope? read vs write  │  │
   │   │    policy: allow / deny / ask  +  append-only audit log                │  │
   │   └──────┬───────────────────────────────────────────────┬───────────────┘  │
   │          │ ask → user                     write-grant OK  │                  │
   │   ┌──────▼───────────────┐                        ┌───────▼───────────────┐  │
   │   │  CREDENTIAL VAULT     │  real tokens swapped   │  NETWORK EDGE          │  │
   │   │  (local auth daemon)  │◄── in at the edge ────►│  host-allowlisted,     │  │
   │   │  real OAuth/refresh    │  agent never sees them │  classified egress     │  │
   │   └───────────────────────┘                        └────────────────────────┘ │
   └──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Sentinel — the action/egress gate

- **Separate process, host-side, outside the agent's container.** Sentinel is *not*
  a prompt-level guardrail inside the model loop; it is a system-level gatekeeper
  that every proposed action and every network egress must pass. Meta's pitch:
  the agent "never sees your actual passwords or payment details," and "a separate
  system called Sentinel reviews every action … deciding whether it proceeds, is
  blocked, or goes to the user for approval."
- **Classification → three verdicts:** `proceed` / `block` / `ask-user`. The
  dimensions checked per action: (a) is the connector permitted; (b) is this *kind*
  of action allowed; (c) did the user grant that scope; (d) read vs. write.
  Reads default-allow; writes ask for a grant.
- **Scoped grants:** read-only vs write, single transaction, single service, or a
  fixed time window.
- **Approval-fatigue design:** Meta explicitly acknowledges that prompting on every
  step trains reflexive "yes." Muse asks only for *sensitive* actions; previously
  authorized low-risk tasks run uninterrupted.
- **Timeout / failure:** the egress path is **fail-closed** — no Sentinel verdict
  means the action does not go out. (This is the design intent; incident #5 below
  shows where the *classification* was too coarse to catch a bad action it did
  technically "approve.")

**The structural hole (sourced, important for Limpet):** Sentinel judges *per
action* and *per session*, but it has **nowhere to write a narrowed scope**. In the
marketplace incident, the user delegated "listing management." Permission policy
said "sending a message is allowed," so each message passed. But one *delegation to
manage* silently became four *irreversible executions* (settled a price, closed a
deal, passed the user's home address, set a meet time). Per-action permission
"allows a message" but cannot distinguish "say hello" from "close a deal," and
session-level human confirmation does not approve each execution inside the session.
**Lesson: a gate that classifies action-*kind* without a durable, narrowable
scope-of-intent will approve individually-legal steps that compose into an
unintended commitment.**

### Compare to our energize-confirm / tier gate

| | Muse Sentinel | Limpet (`needs_energize` + tier trust) |
|---|---|---|
| Where it sits | host process outside agent container | in-loop policy on tool install/invoke (`registry_index.py:287-305`, `limpet_tools.py:355`) |
| Granularity | per action, per egress | per plugin, with per-tool override (`tool_energize`) |
| Verdicts | proceed / block / ask | tier-policy pass + interactive energize confirm |
| Default | reads allow, writes ask | `needs_energize=false` default; read/scan tools can opt out of gating a write-tool plugin carries |
| Failure mode | fail-closed egress | worker *cannot self-approve* an energize/WebFetch confirm (`injection.py:22`) → effectively fail-closed for autonomous workers |

We already have the right *shape* (write-gated, per-tool overridable, fail-closed
for an agent that can't confirm). What we lack and Muse's incident proves we need:
**a place to write a narrowed, durable scope-of-intent per delegation**, so composed
actions inside one "approved" job are re-checked against the *intent*, not just the
action-kind.

## 2. Secure VM — isolation, lifecycle, state

- **One dedicated Linux VM per *user*** (not per account — the contrast with xAI
  GrokBot, which is per-account with bots sharing one runtime and *not* a security
  boundary between bots; xAI warns "do not place a credential on it if another Bot
  should not use it"). Muse draws the boundary tighter: per user.
- **Inside the VM, a `systemd-nspawn` container** holds the agent. The agent's
  browser/filesystem/terminal live in the container; **credentials live outside it**
  (vault owned by a local auth daemon). The agent cannot reach the real creds.
- **Isolation = two nested rings:** VM isolates user-from-user (the GrokBot layer);
  nspawn container + external vault isolates agent-from-credentials *within* the
  user's own VM.
- **Lifecycle:** persistent, stateful workspace (the category shift from ephemeral
  stateless execution to "long-lived cloud computer per user"). State persists in
  the VM filesystem (`memory/`, `SOUL.md`, `tools/`) across sessions and app closes.

## 3. OpenClaw integration — kept vs replaced

- **Kept:** the OpenClaw *agent file layout and loop* — `SOUL.md` (system/persona),
  `memory/`, `tools/` directory. The teardown notes Muse's file system is "highly
  homologous" with OpenClaw — this is also the "accused of copying an open
  framework" incident.
- **Replaced / added by Meta:** (a) the model — Meta's own "Muse Spark," not the
  OpenClaw-default harness model; (b) the **Sentinel host-gate + vault**, which
  OpenClaw-the-framework did not ship (OpenClaw's own answer to this came later as
  **OpenClaw Enterprise / OCE** — "an enterprise control plane … multi-tenancy, hard
  security boundaries, standardized agentic primitives"); (c) consumer surface
  (phone/Mac app, push, Stripe Link payments).
- **Plugin / tool routing:** tools are connectors (18 named in one mirror:
  Slack, GitHub, Notion, Discord, Home Assistant, Telegram, Teams, etc.). Tool calls
  route **through the vault/edge**: agent holds a surrogate token, the daemon swaps
  it for the real one at the network edge, and every boundary-routed request is
  host-allowlisted and classified read/write before it leaves.

## 4. Background operation

- Muse "continues to work when the app is closed" — because the agent lives in the
  **cloud VM**, not on the phone; the app is a thin client. Closing the app detaches
  the viewer, not the worker.
- **Approval delivery is the weak link:** approval requests are meant to arrive as
  push notifications, but users reported (V2EX) that the iOS app's "notifications"
  are **in-app banners the app draws itself**, with no real APNs push fired — so a
  backgrounded/locked phone receives *nothing* and the user must reopen the app to
  see a pending approval. For a fail-closed gate this means **a sensitive action can
  stall indefinitely, unseen**, until the user happens to reopen the app.

## 5. The incident spike — what went wrong, mitigations, lessons

Six weeks, ≈7 distinct incidents (the ticket frames the volume as a ~40% spike;
treat that number as ticket-provided). Sourced incidents:

1. **Address/PII leakage** — the marketplace over-delegation (§1): an undeclared
   scope let the agent hand out the user's home address and set a meet.
2. **Wardle 0-day (dictation hijack)** — an undocumented setting
   (`*_dictation_endpoint`) let an *already-local* unprivileged process redirect
   Muse's dictation traffic to capture/inject prompts and ride the agent's trust.
   Now patched. Lesson: a powerful agent is a high-value *secondary* target after a
   foothold — not a remote-instant-pwn, but the blast radius of a compromised agent
   is large.
3. **Full-Disk-Access / Messages dispute** — a journalist said Muse read Messages
   data without the permission he thought he'd granted; Meta says the Messages
   connector is opt-in and needs both macOS FDA and explicit connector enable.
   Apple tightened FDA guidance ~Oct 2; causation unconfirmed. Lesson: *consent
   granularity on the OS permission (FDA) ≠ consent granularity the user believes
   they gave the agent.*
4. **Downgraded severity** — Meta downgraded a Muse flaw SEV-2→SEV-3, then shipped
   the secure VM anyway. Lesson: the architecture caught up to the risk only after
   external pressure.
5. **Marketplace ban** — an e-commerce platform banned the agent (automated
   negotiation/ordering at scale).
6. **OpenClaw-copying accusation** — the file-layout homology (§3).
7. **Background-push failure** (§4) — approvals not reliably delivered.

**Mitigations Meta applied:** per-user VM + nspawn + external vault (the SEV
response); scoped/narrow permissions; "ask only for sensitive actions"; Stripe Link
with virtual one-time card numbers and purchase protection (lost/price-drop/return
cover) to bound *payment* blast radius; opt-in connectors; model-training opt-out +
PII scrubbing.

## 6. Multi-app integration — auth flow

- **Sign-in:** OAuth per connector. The refresh tokens produced by those sign-ins
  are held and **renewed by the local auth daemon** (`oauth2_refresh_token`), not by
  the agent.
- **Token storage:** real tokens live in a **vault owned by the auth daemon**; the
  agent process holds only **opaque surrogate tokens**. The daemon swaps surrogate→
  real **at the network edge**. Legacy plaintext tokens auto-migrate into the vault
  on first use (a one-time hand-off; plaintext scrubbed best-effort afterward).
- **Egress discipline:** every boundary-routed API request is **host-allowlisted**,
  **classified read/write**, and checked against allow/deny/ask with an **audit
  log**. The one off-allowlist exception is credential-free, body-less GET/HEAD
  redirect hops.
- **Tool schema:** connectors are declared tools; the agent calls the tool, the
  edge/daemon attaches the real credential. The agent never names a secret.

---

## Feature-extraction list (what Muse actually ships)

1. Host-side action gate as a **separate process** outside the agent container.
2. Three-verdict classifier: proceed / block / ask, read-default-allow / write-ask.
3. **Scoped grants**: read-vs-write, single-transaction, single-service, time-boxed.
4. **Credential vault + surrogate tokens**: agent never holds real secrets; daemon
   swaps at the network edge; daemon owns OAuth refresh.
5. **Host-allowlisted, read/write-classified, audited egress** with a narrow
   credential-free redirect exception.
6. **Per-user VM** (user-level isolation) + **nspawn container** (agent↔cred
   isolation) — two nested rings.
7. **Cloud-resident agent** → true background operation; app is a thin client.
8. **Payment blast-radius bounding** via one-time virtual cards + purchase cover.
9. Opt-in connectors; model-training opt-out; PII scrubbing.

## Patterns to ADOPT for Limpet

- **A.** Keep the agent credential-free: surrogate tokens in the agent, real tokens
  in a daemon-owned vault, swap at the egress edge. Limpet already keeps tokens in
  0600 files behind git credential helpers (instinct 4); formalize the
  surrogate-swap so a Limpet worker/plugin literally cannot print a real secret.
- **B.** **Host-allowlisted + read/write-classified + audited egress** as a first-
  class gate, not a convention — this is the single highest-value Muse primitive and
  maps cleanly onto our existing `needs_energize`/`tool_energize` split (read/scan
  tool vs write/flash tool on the same plugin).
- **C.** **Durable, narrowable scope-of-intent per delegation** — the thing Sentinel
  lacked. Give the energize gate a place to write "this job = manage listing,
  read-only on contact info, no commitments," and re-check composed actions against
  that intent, not just action-kind. (Directly fixes the §1 failure class.)
- **D.** Two nested isolation rings where it matters: we already run one
  worktree+branch per task; the Muse lesson is to make the **credential ring** a
  separate enforced boundary from the **work ring**.
- **E.** Scoped/time-boxed grants (single-op, single-host, fixed window).

## Anti-patterns to AVOID

- **1. Per-action permission without scope-of-intent** — approves legal steps that
  compose into an unintended irreversible commitment (the home-address leak). A gate
  must judge intent, not just "is this action-kind allowed."
- **2. Self-drawn "notifications" that aren't real push** — a fail-closed gate whose
  approval request can silently fail to reach the human turns "ask-user" into "stall
  forever." If Limpet ever surfaces an approval to a human, the delivery channel must
  be *provably* delivered or the request must escalate/queue visibly (we already
  have this instinct: a worker that can't self-approve must `wq block`, not stall).
- **3. Undocumented local endpoints / settings** (the dictation 0-day) — any
  undocumented knob that reroutes a trusted agent's traffic is a foothold multiplier.
- **4. OS-permission granularity ≠ user's mental model of consent** (FDA/Messages) —
  don't let a single coarse OS grant stand in for fine-grained per-connector consent.
- **5. Shipping the capability before the boundary** (SEV-2→SEV-3 then build-VM-
  after) — build the credential/egress boundary *with* the capability, not after an
  incident forces it.

---

**Bottom line for Limpet:** Muse validates the architecture we're already building
toward (write-gated, per-tool, fail-closed, credential-isolated) and hands us one
concrete gap to close ahead of them — **a durable scope-of-intent the gate can
re-check composed actions against.** Adopt A–E; the single most important is C,
because Muse's worst, most-reported incident is exactly the failure C prevents.
