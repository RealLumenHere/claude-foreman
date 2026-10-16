# wq#4059 — LIMPET DESIGN: sentinel-agent pattern for the energize-confirm gate

**Verdict: ADOPT, with the gate split into a separate `limpet-sentinel` daemon.**
The current in-meta-server gate is a confused-deputy waiting to happen: the process
that can be prompt-injected is the same process that decides whether an energizing
action is allowed. Moving the decision into a minimal, LLM-free, separately-privileged
daemon removes that single point of compromise for a small, well-bounded amount of code.
This evaluates the split, states the threat models it defends against, resolves the two
hard interactions the ticket flags (headless/batch, SSH), and gives the spec deltas.

This is a **design-only** ticket. Nothing here installs a daemon, opens a socket, or
changes a running gate; it specifies the pattern and its contract, slotted into hooks the
architecture already defines: the energize-confirm gate
(`design/wq3806_limpet_architecture.md:214-228`), the meta-server as the sole tool router
(`:123-170`), and the wizard's §4.3 confidence gate
(`docs/target-wizard-spec.md:171-184`). It does not restate them.

## 0. Prior art and threat model (read first)

### 0.1 Prior art — Meta Muse Sentinel (the ticket's named reference)

Live-search doctrine applies (this is a "how do we solve X" / design ticket). Fleet-search
(SearXNG, 2026-10-15) ran; `brave`/`duckduckgo`/`startpage` were rate-limited/CAPTCHA'd
(the standing wq#2595 condition), `google cse` answered. The design-detail pages
(explainx.ai, forkast.news) were behind this worker's missing `WebFetch` grant, so the
account below is grounded in the returned snippets and the ticket's named sources; exact
long-quote grounding of the Muse security whitepaper is deferred to the build ticket rather
than fabricated here (same honesty posture as `docs/agent-security-spec.md` §0.1).

What the public record converges on for **Meta Muse** (launched 2026-09-08, built on
OpenClaw; Muse Secure VM, one confidential VM per user, `systemd-nspawn` sandbox):

- **Sentinel is a separate process, not a module.** It is "an independent gatekeeper…
  the sole authority for connector actions and network egress, enforced at the boundary"
  (explainx.ai, itadon.com, 2026-09). The model/agent proposes; Sentinel disposes.
- **Credentials never reach the agent.** They live in storage owned by a separate
  `hatch-authd`, which mints surrogate tokens; a separate Sentinel agent owns every
  egress (orcarouter.ai, 2026-09). The agent holds capability tokens, never raw secrets.
- **Minimal, auditable enforcer.** Sentinel is described as a small audit/policy agent that
  owns the decision so that a compromise of the large model-bearing process cannot by
  itself perform a gated action.

The transferable idea is **privilege separation of the decision from the proposer**: the
component that reads hostile input and runs an LLM must not also be the component that holds
the authority to act irreversibly. This is the classic confused-deputy / reference-monitor
split (Saltzer & Schroeder: *least privilege* + *complete mediation* + *economy of
mechanism*), which Muse applies to egress and Limpet should apply to hardware energizing.

### 0.2 The asset Limpet is protecting is different from Muse's

Muse gates **network egress and connector writes** (data exfiltration, unwanted posts/sends).
Limpet gates **physical, irreversible action on attached hardware**: probe power, JTAG/SWD
drive, flash writes, GPIO/reset, bootloader-unlock, and boot-vector/exploit actions from
`limpet/data/soc-exploit-db/` (`design/wq3806_limpet_architecture.md:216-218`). The blast
radius of a false "confirm" is a bricked or maliciously-reflashed target device, not a leaked
message. That raises, not lowers, the value of separating the deputy — the thing we are
guarding is the one thing in the system that cannot be rolled back from `workspace.git`.

## 1. Current design and its single point of failure

Today the energize-confirm gate lives **inside the MCP meta-server**
(`design/wq3806_limpet_architecture.md:230-231`: "Every HAL capability is mediated by the
meta-server like any other tool, so hardware actions are trust-gated and audited identically
to software tools"). The meta-server is also:

- the tool router that mounts Ghidra/REA/HAL children (`:123-142`),
- the process that reads **attacker-controlled tool output** — strings, decompilation,
  disassembly — which is explicitly hostile input aimed at the LLM
  (`docs/agent-security-spec.md:1-10`), and
- the process that loads plugins, some of which are only `community`-tier.

So the gate's integrity depends on the integrity of the single largest, most-exposed,
most-frequently-changing process in the system. The three realistic compromise paths from
the ticket are all paths **into the meta-server**:

1. **Indirect prompt injection** through analysis target bytes (`agent-security-spec.md`
   is the whole defense for this, and it is a mitigation, not a guarantee).
2. **Plugin exploit** — a `community` or even `vetted` plugin child with a memory-safety bug
   or a malicious manifest running in or alongside the router.
3. **Memory corruption** in the router or a native backend (Ghidra/QEMU are large C/C++/Java
   surfaces).

In every one of these, the attacker who lands code or steers the model **is already inside
the process that owns the confirm decision**. A gate implemented as an `if action_class ==
energizing: require_confirm()` branch in that process is bypassed by the same bug that
compromised the process — flip the branch, forge the confirm, or call the HAL child directly.
The gate and the thing it guards share a fate.

## 2. Proposed design — `limpet-sentinel` daemon

Split the *decision* out of the meta-server into a separate, minimal, differently-privileged
daemon. The meta-server keeps routing and audit; it loses the authority to energize unilaterally.

```
 meta-server (big: LLM, MCP, plugins, hostile input)
   │  AF_UNIX stream, SO_PEERCRED-verified
   │  EnergizeRequest{action_class, capability, target_id, args_digest, workspace, nonce}
   ▼
 limpet-sentinel (small: policy only — no LLM, no MCP, no plugins, no net)
   │  classifies action_class, prompts the operator, decides
   ▼
 ConfirmToken{request_hash, exp, sig}   ── or ── Deny{reason}
   │
   ▼
 HAL executes the energizing capability ONLY on presentation of a sentinel token
 whose request_hash matches the exact call (capability+args_digest+target+nonce).
```

### 2.1 The five ticket requirements, resolved

1. **Separate daemon owning the decision.** `limpet-sentinel` receives `EnergizeRequest`s,
   classifies `action_class` from the capability manifest (defaulting ambiguous/missing to
   `energizing`, fail-closed, same rule as `:222-224`), prompts the operator on energizing
   actions, and returns a signed confirm token or a deny. The meta-server/HAL **cannot
   execute an energizing capability without a matching, unexpired token**. Read-only
   capabilities never touch the sentinel path and stay fully autonomous (`:223-226`).

2. **Different Unix user, no device write access.** Sentinel runs as `limpet-sentinel`,
   **not** in the `plugdev`/`dialout`/device groups the HAL needs for probe/serial/USB
   access. The meta-server (or a HAL executor) holds the device privilege; the sentinel
   holds the **signing key for confirm tokens** and nothing else. This is the crux: the
   privilege to *decide* and the privilege to *act* are now held by two different uids, and
   neither process holds both. Only `limpet-sentinel` can mint a valid token; only the
   HAL-privileged process can drive hardware, and it refuses to drive without a token.
   Compromise of one uid does not yield the other's authority.

3. **Unix socket + `SO_PEERCRED`.** Communication is an `AF_UNIX` stream socket owned by
   `limpet-sentinel` (mode `0660`, group-restricted). On accept, the sentinel reads
   `SO_PEERCRED` and verifies the caller's **uid is the meta-server's service uid** and
   (optionally) that its pid maps to the expected cgroup/unit. This authenticates *which
   local process* is asking, not just that someone local is. A rogue process under a
   different uid cannot request a confirm. (Peer-cred is the right primitive precisely
   because it is kernel-attested and unspoofable from userspace, unlike any token the
   caller could present.)

4. **Minimal attack surface.** The sentinel is **policy only**: parse a fixed-schema
   request, classify, prompt, sign, log. No LLM, no MCP server, no plugin loader, no network
   socket, no filesystem writes except its append-only audit log. Target it at a few hundred
   lines in a memory-safe language, no dynamic dispatch on request contents. *Economy of
   mechanism* is the whole point — it is small enough to read in one sitting and verify,
   which the meta-server will never be.

5. **Comparison — what separation buys (see §3).**

### 2.2 Token binding (the part that makes it actually work)

A naive "sentinel returns yes/no" is bypassable by a compromised meta-server that just
fabricates a "yes" or replays an old one. The confirm must be a **capability token bound to
the exact call**:

- `ConfirmToken = sign_sentinelkey(request_hash = H(capability, args_digest, target_id,
  workspace, nonce, exp))`.
- The HAL executor (device-privileged) recomputes `request_hash` from the call it is about
  to make and verifies the signature with the sentinel's **public** key before energizing.
- `nonce` is single-use (sentinel records issued nonces); `exp` is short (seconds). This
  kills replay and "confirm for action A, execute action B" substitution.

Without this binding the split is theater. With it, a fully-compromised meta-server still
cannot energize: it can relay a request and relay a token, but it cannot forge one, cannot
reuse one, and cannot redirect one to a different action than the operator saw.

## 3. Threat-model comparison: in-meta-server gate vs. separate sentinel

| Threat | In-meta-server gate | Separate `limpet-sentinel` |
| --- | --- | --- |
| **Indirect prompt injection** steers the agent to energize | Bypassed — the injected instructions run in the process that owns the confirm; the model can be talked into "confirming" | **Defended** — the operator, not the model, answers the sentinel prompt; the compromised model cannot mint the token. Injection can *request* an energize but cannot *grant* it |
| **Plugin exploit** (RCE in a `community`/`vetted` child) | Bypassed — code exec in/near the router can flip the branch or call the HAL child directly | **Defended for the energize authority** — the plugin runs under the meta-server uid, which has no signing key; it still cannot forge a token. (It may hold device privilege, so see §3.1) |
| **Memory corruption** in router/backend | Bypassed — same process, same fate | **Defended** — corrupting the meta-server does not reach the sentinel's address space or key |
| **Forged / replayed confirm** | N/A (no token) — any code in-process "is" the confirm | **Defended** by token binding (§2.2): single-use nonce, short exp, request-hash signature |
| **Operator fatigue / silent auto-confirm** | Easy to regress — a code change can quietly default-confirm | **Harder to regress** — auto-confirm would have to live in the audited minimal sentinel, a conspicuous change in a small reviewed file |
| **Audit integrity** | Audit log written by the same process that can be compromised | **Stronger** — the sentinel keeps its own append-only decision log under its own uid; the record of *who confirmed what* survives a meta-server compromise |
| **Operator is the target** (social-engineered into confirming) | Not defended | **Not defended** — separation does not help here; the sentinel prompt must show the *exact* bound action (capability, target, args summary) so the human sees what they approve (§4) |

### 3.1 The honest limit: the executor still needs device privilege

Separation protects the **authority to decide**. It does **not** by itself protect the
**ability to act** if the device-privileged executor is itself the compromised component. If
the HAL executor runs inside the meta-server and that process has `plugdev`/`dialout`, then
RCE in the meta-server can drive hardware **without asking the sentinel at all** — it just
opens `/dev/ttyUSB0` or the probe directly.

So the split is only as strong as a second decision: **the device-privileged code must also
be minimal and separate from the LLM/plugin surface.** Two coherent end-states:

- **(a) Sentinel-gates-a-thin-executor.** A small `limpet-hal-exec` holds device privilege,
  exposes only "execute this capability *if* accompanied by a valid sentinel token," and is
  itself LLM/plugin-free. The big meta-server holds **neither** the signing key **nor**
  device privilege — it only relays. This is the strong form and the one to build. It
  mirrors Muse's `hatch-authd` (holds secrets) + Sentinel (holds decision) + agent (holds
  neither) triangle.
- **(b) Sentinel-gates-the-meta-server-as-executor.** Simpler, weaker: the meta-server keeps
  device privilege and merely *voluntarily* checks the token. This defends against prompt
  injection that steers the *model* (the dominant, highest-probability threat) but not
  against RCE in the meta-server. Acceptable as a phase-1 if (a) is scheduled.

**Recommendation: build toward (a).** Phase-1 may ship (b) to get the injection defense
immediately, but the HAL executor must be carved out to its own uid for the guarantee to
hold against plugin/memory-corruption RCE, not just against a talked-into-it model.

## 4. Interaction with headless/batch mode and SSH (ticket points 6)

This is where a naive sentinel breaks Limpet, so it needs an explicit answer. Limpet's
primary face is **headless, agent-driven, over SSH** (`docs/target-wizard-spec.md:230-256`;
instinct-1: the box is operated entirely over SSH). A sentinel that always blocks on an
interactive GUI dialog would make every energizing action un-runnable headless — a
regression, not a feature.

Resolution — **the sentinel's job is to obtain an operator decision, not to own a GUI**:

1. **The decision channel is pluggable; the authority is not.** The sentinel accepts a
   confirm through any of: a GUI dialog (`docs/agent-chat-spec.md:157-171` hardware events),
   a TTY prompt on an attached interactive SSH session, or a **pre-authorization**. What it
   will never do is let the *meta-server* answer on the operator's behalf.

2. **Headless/batch = pre-authorization, scoped and signed, not auto-yes.** Before a batch
   run, the operator issues a **confirmation grant** to the sentinel out-of-band (a signed
   CLI call under a human-controlled session): "for workspace W, target T, permit capability
   class C up to N times / until time E." The sentinel honors grants that match and denies
   everything else. This maps exactly onto the existing headless `--confirm` flag
   (`docs/target-wizard-spec.md:245-247`) and the operator-override-is-logged-with-its-actor
   rule (`:184`) — except the grant is now held and enforced by the sentinel, not asserted by
   a flag the meta-server reads itself. The grant is the headless analogue of clicking the
   dialog, pre-committed and bounded, so autonomy on the read-only loop is preserved while
   the irreversible action still required a *human* act — just an earlier one.

3. **SSH sessions.** An interactive SSH session can carry the prompt (sentinel writes to the
   controlling TTY it was told to use, operator types y/n). A **non-interactive** SSH/batch
   invocation (the fworker case, no human on the line) has **no TTY and must not block** — it
   runs only what a pre-authorization grant already covers, and any ungranted energizing
   action **fails closed with a legible "needs confirmation grant" error and exit non-zero**
   (same shape as the wizard's `--confirm`-absent path, `:245-247`). This is the right
   behavior for the fleet's own autonomous workers: they energize nothing they were not
   pre-granted, and they get a clear signal instead of a hang.

4. **No conflict with instinct-3 autonomy.** Instinct-3 autonomy is about *not gating on
   Frank for software decisions*; the energize-confirm gate is explicit standing policy
   (Frank, 2026-10-15, `:214`) that irreversible hardware action is the one thing that *does*
   require a human confirm. The sentinel does not expand that gate — it hardens it — and the
   pre-authorization grant is how a human pre-commits so autonomous batch work is still
   possible. The two are consistent: autonomous on read-only, human-gated on energize, and
   the grant lets the human gate *ahead of time* for a bounded batch.

## 5. Recommendation

**Adopt the sentinel pattern.** Specifically:

1. Split the energize-confirm **decision** into a `limpet-sentinel` daemon: separate uid
   (`limpet-sentinel`, no device groups), `AF_UNIX` + `SO_PEERCRED`-verified socket, no LLM,
   no MCP, no plugins, no network, append-only own audit log.
2. Make the confirm a **call-bound, single-use, short-lived signed token** (§2.2), not a
   boolean — without binding, the split is defeated by a compromised relayer.
3. Carve the **device-privileged executor** (`limpet-hal-exec`) out of the meta-server too
   (§3.1 end-state (a)); otherwise the split defends only against model-steering injection,
   not against RCE in the meta-server. Phase-1 may ship the weaker (b) to get the injection
   defense now, provided (a) is the committed target.
4. Headless/batch works via **scoped, signed pre-authorization grants** held by the sentinel
   (§4), not auto-confirm; non-interactive invocations fail closed with a legible error.

**Cost/benefit.** The added mechanism is one small daemon, one socket protocol, and a
token-verify step in the executor — a few hundred lines, no LLM, no new heavy dependency.
Against that: it removes the single most dangerous property of the current design — that the
process most exposed to hostile input is also the process authorized to take the one
irreversible physical action in the system. For a tool whose whole safety story is the
energize-confirm gate (`docs/limpet_vision.md:44-46,147`), letting that gate share a fate
with the LLM router is the wrong trade. **Adopt.**

**What it does not fix (state plainly):** a social-engineered operator who confirms a
malicious action (mitigate by showing the exact bound action in the prompt, §3 last row);
and it does not remove the need for `agent-security-spec.md`'s sanitization layer — injection
defense-in-depth still matters because the sentinel reduces the *consequence* of a steered
model, not the steering itself.

## 6. Spec deltas if adopted (for the build ticket, not applied here)

The ticket says "if adopted, update `hal-spec.md` S9 and `agent-chat-spec.md`." Note for the
build ticket: **`hal-spec.md` does not yet exist** — the HAL and the energize-confirm gate
currently live in `design/wq3806_limpet_architecture.md` §4 (`:214-231`), which
`docs/limpet_readme.md:326,375` forward-references as the future `docs/hal-spec.md`. The
delta below therefore targets wq3806 §4 (the live home) and names the future §S9 slot:

- **HAL spec (`wq3806` §4 / future `hal-spec.md` §S9 "Sentinel-gated energizing").** Replace
  "the meta-server must refuse to invoke an `energizing` capability without an explicit
  operator confirm attached to that specific call" (`:219-221`) with: the **HAL executor**
  must refuse to invoke an `energizing` capability without a valid, call-bound, unexpired
  **sentinel confirm token** (§2.2); the meta-server relays the request and the token but
  holds neither the signing key nor (end-state) device privilege. Add the daemon's
  uid/socket/`SO_PEERCRED`/no-LLM constraints (§2.1) and the fail-closed default.
- **Chat spec (`agent-chat-spec.md` §4.3 Hardware events, `:157-171`).** Add a
  **confirm-prompt event**: an energizing action pending a sentinel decision renders as an
  explicit confirm dialog/`system_event` showing the **exact bound action** (capability,
  target-id, args summary) so the operator sees what the token will authorize; the operator's
  y/n is delivered to the sentinel, never synthesized by the session service. In headless
  mode the same event renders as the "needs confirmation grant" non-zero exit (§4.3).

These are deferred to a build/provisioning ticket, consistent with how `wq3806` §7 and
`agent-chat-spec.md` §9 both scope their build steps out of the design ticket.

---
*Evaluation only. No daemon, socket, or gate change is created by this ticket. Prior art:
Meta Muse Sentinel / Muse Secure VM (ai.meta.com/muse; Reuters, Khaleej Times, Sep 2026;
fleet-search 2026-10-15). Refs: wq#4059.*
