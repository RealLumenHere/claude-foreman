# wq4856 — Octopus-SI-OS architecture dissection for Limpet OS primitives

**Source:** https://github.com/miuuyy/Octopus-SI-OS (MIT, research preview, 1 commit, cloned 2026-11-05)
**Scope read:** docs/architecture.md (643 lines), security-model.md, scenarios.md, all 12 docs/contracts/*.md (~4,200 lines total), crate layout (20 crates, 59,091 lines of Rust by `wc -l` over crates/**/*.rs).
**Verdict up front:** the product (macOS desktop app, ChatGPT/Codex provider) is useless to the fleet, as the ticket predicted. The architecture is genuinely worth mining. Eight primitives compose cleanly, and four of them — policy formed fresh at every arrival, typed refusals as first-class ledger rows, runs as parked data rather than processes, and "the path is the permission" — solve problems Limpet and the foreman harness currently solve ad hoc.

---

## 1. What Octopus actually is

A ~59k-line Rust kernel whose only verb is "an artifact crossed an interface." Everything else — models, chat channels, sensors, tool sandboxes, workers, memory, schedulers — is a JSON descriptor folder plus optional driver code in any language. The kernel never reads a payload, never branches on a worker/provider/model name, and never trusts anything a model wrote. Design slogan: *small in primitives, large in descriptors* — "every time a capability is added by declaring something instead of coding a branch, the system gains one more thing it can inspect, replay, and change about itself" (architecture.md:26-30).

Two sides, defined by control not location: **System** (everything Octopus can place runs in) and **World** (everything else; artifacts in/out through declared interfaces, never a second execution graph outside). Coordination with external services is always World → System → World.

## 2. The eight primitives, dissected

### 2.1 Interface — the typed port

One abstraction for everything: model providers, chat channels, microphones, tool-room doors, and workers are all interfaces. Contract: **artifact in, artifact out**; the runtime never looks inside.

- **Identity is the folder** (`registry/interface/<id>/`), and nothing else. No wildcard families: two private conversations are two folders, which is how thread isolation falls out of identity instead of access rules (architecture.md:131-136).
- Classifiers (`interface_type`, `role`, `direction`) describe; they never route or widen a binding (INTERFACES.md:36-41).
- An interface with a process declares it in `life.json` with an explicit authority block (read/write/exec paths, network, IPC) and starts fail-closed with a cleared environment. Liveness comes only from bindings: an installed interface nobody binds does not run (INTERFACES.md:86-119).
- The interface decides where raw input becomes an artifact (wake-word threshold on raw audio — architecture.md:136-137). Sensors don't flood the kernel.
- Model providers are explicitly "drivers": new provider over an existing wire = descriptor only; new wire = driver code once, then every later provider over it is data (INTERFACES.md:174-192). MCP is *not* a framework layer — an MCP server is one interface among the rest.

### 2.2 Artifact — the only boundary-crossing unit

Message, diff, screenshot, tool call, tool result, memory pack, evidence — all the same thing. What crosses is a **manifest**: a self-identifying name (the emitting interface's own vocabulary), a payload reference pinned by content hash, and delivery metadata. Payload stays wherever the emitter put it; the kernel fails closed on hash mismatch (ARTIFACTS.md:13-27).

- **No runtime `kind`.** Per movement the kernel sees exactly three things: route, caller, movement. Admission by payload type does not exist; the receiver's own code decides what it accepts (ARTIFACTS.md:114-133).
- Permissions ride the **invocation envelope** the kernel attaches, never the payload: "Adding `"grant": {...}` to the transcript would grant nothing" (ARTIFACTS.md:94-95). This is the cleanest prompt-injection firewall statement in the codebase.
- **Fan-out:** an arrival goes to every active checkpoint in any run that pins that interface folder and can take the name; each edge gets its own grant and its own verdict. A refused edge never poisons sibling edges (ARTIFACTS.md:146-170).

### 2.3 Run — durable execution as data

A run is a JSON record: which worker pin it wears, which EE it lives in, its checkpoints. **"A run is data, not a process. It holds no thread and no lease while it waits. It does not really start or end; it idles, parks on a checkpoint, and wakes when the awaited artifact lands"** (architecture.md:172-175).

- A **checkpoint is an artifact crossing an interface**. Awaiting a model reply, awaiting another run's result, and reaching another run are three faces of one primitive: a binding on a checkpoint (architecture.md:164-170).
- Consequence chain: nothing blocks → a deadlock is two unmet checkpoints visible as data → the fix is a granted JSON edit → "there is no deadlock detector and no watchdog, because there is nothing to hang" (architecture.md:427-430).
- Restart/recovery is therefore not a subsystem: scan the JSON, wake every interface on a live binding, each catches up from its own ledger cursor. Commands survive runtime restarts in a separate keeper process and results are re-delivered exactly once per call (architecture.md:432-473).
- The agent loop is not a mechanism: tool call artifact continues the turn, reply artifact ends it. Spawning = emitting to a worker's interface; escalating = emitting through a binding your creator left; scheduling = an interface that emits at a time; waiting = an unmet checkpoint (architecture.md:256-283).

### 2.4 Execution Environment — goal + policy, nesting narrows

An EE answers *why* and *what is allowed*. Instances nest; an inner EE is only ever narrower. Root goal is `existence`; `project`, `memory_maintenance`, `self_improvement` are registry templates, not constants (architecture.md:201-217). Distinct from the **worker environment** (room), which answers *where the effect physically happens* and exists only when tools must touch the world (ENVIRONMENTS.md:6-12). A room is exactly: a kind (executor), a door interface, and formed policy. Rooms ship as kinds: `filesystem_workspace`, `git_worktree`, `browser_session`, `desktop_session`, `container_session`, `remote_exec_server`, `mcp_stdio_session`, `system_room` (the system's own files), etc. (ENVIRONMENTS.md:90-105). A kind declares `boundary_defaults` (read/write roots, protected subpaths, network ceiling); policy can only narrow it — "there is no operation that widens it" (ENVIRONMENTS.md:182-187).

### 2.5 Worker — two JSON files, not a program

"The agent stops being a program. The loop it used to own is expressed by the architecture, and what remains is a formation: a few JSON lines saying which interfaces, tools, rooms, and proofs it may ever hold" (WORKER_TYPES.md:3-7). A worker is a System interface folder holding `interface.json` (the addressable pin) and `policy.json` (the **ceiling**: max interfaces, tools, room kinds, mark outcomes). A new worker is a copied folder with edited lists; zero Rust. Role/prompt are not fields — they are bound context artifacts (`role_artifacts`), carried through the same generic path as any artifact, never a branch on a worker's name (WORKER_TYPES.md:107-113). Model choice is a run binding, not a worker property; switching provider is a couple of JSON lines on the run (WORKER_TYPES.md:129-134).

### 2.6 Policy — never stored, formed at every arrival

The core invention. `grant = worker ceiling ∩ EE chain ∩ run's authored bindings`, recomputed by a deterministic script at **every** artifact arrival (POLICY.md:71-84, 224).

- **Nothing a model writes becomes a right**, because the grant is re-formed from descriptors, never read back from the run record (security-model.md:19-23).
- **Who created the run is not an input.** A narrow run can open a coding EE and a capable coder runs inside it; capability comes from place, not parentage. No clamp-under-the-creator, no reach graph, no precomputed subtrees (POLICY.md:245-262).
- Design test: does it still hold at a thousand nested EEs, each handing workers a minimal non-conflicting grant? (POLICY.md:10-18).
- Replayable because formation is pure: inputs are hash-pinned descriptors whose normalized bytes are retained in the blob store. Same inputs, same grant (POLICY.md:36-44).
- Static/dynamic split: **boundary** (ceiling, in the descriptor, answers "possible at all?") vs **context** (formed per run, answers "granted now?") (POLICY.md:86-133).
- Asking for more is fail-closed and observable: dead bindings are stripped at formation; out-of-grant emission is a typed `DeliveryRefused` row, not a silent drop (POLICY.md:311-324).
- **The path is the permission:** run records, EE records, registry descriptors are plain files; the right to change one is a room whose path covers it. Self-rearming, delegation, even moving a run between EEs are ordinary gated file writes (POLICY.md:326-331, ENVIRONMENTS.md:199-205).

### 2.7 Mark — proof recorded only after a verifier passes

Checkpoint authoring declares `required_verified_artifacts`: label + verifier + params + mark id. Verifier = removable script package (any language, stdin request → stdout verdict, sandboxed, no network unless declared, command never comes from params). Pass → runtime records `MarkOutcomeAdmitted`; fail → `VerificationFailed` row, checkpoint stays open, sender decides whether to retry. **Nothing emits marks — not the model, not the interface; there is no mark-emission tool anywhere** (VERIFIERS.md:16-19). Honest limit, stated twice: a mark proves only what its verifier checked; "it builds" is a check, proof a coder stayed on-contract is not designed yet (VERIFIERS.md:203-207).

### 2.8 Ledger — every movement a row, refusals typed

`ledger/movements.jsonl`: append-only, flushed per row, the single source of truth. Six movement kinds (`InterfaceArrival`, `InterfaceOutbound`, `ArtifactMoved`, `MarkOutcomeAdmitted`, `VerificationFailed`, `DeliveryRefused`) and a **closed refusal vocabulary**: `no_binding`, `policy`, `verification_failed`, `unknown_interface`, `unknown_run`, `stale_epoch`, `checkpoint_spent`, `settled`, `malformed` (LEDGER_INDEX.md:44-55). Indexes are caches with OS discipline: an in-memory kernel index for idempotency (never SQLite for kernel decisions) and a disposable SQLite "Spotlight" index for readers in any language — delete it and it rebuilds (LEDGER_INDEX.md:82-136). Live streams are projections over the log, never a second truth.

### 2.9 Self-extension

Two levels, both ordinary work under policy: descriptor-level (a run with a granted registry path writes a new interface/worker/EE folder — plain file edits, picked up live) and source-level (a run edits kernel/packages through a granted workspace) (architecture.md:519-529). The extension cookbook's "What takes what" table has a "Runtime code" column that is **"None" for every row** — provider, channel, worker, goal, tool, room, check, outcome, skill (EXTENSION_SURFACES.md:360-379): "The last column is the point." Registry authority is just path policy: "Do not grant registry paths to environments that also read untrusted input unless you mean to" (security-model.md:75-79). Scenario 1 (docs/scenarios.md:12-40) shows the full loop: a run that can't see authors a contract-with-checkpoints for a coder worker, the coder writes a camera driver as a new interface folder, verifiers gate the marks, descriptors picked up live — *the authored contract is the plan; planning needs no plan engine*.

## 3. Security model in one paragraph

Two independent layers: policy decides what a run may *ask for* (formed at arrival, refusals typed); process authority decides what a process can *physically touch* (deny-by-default OS sandbox per descriptor-declared authority block; macOS `sandbox-exec` in the preview). "Do not treat a policy grant as equivalent to physical process isolation" (security-model.md:17-48). Model untrusted, World artifacts untrusted, runtime "never reads a payload and never takes a model's word for anything." Secrets: descriptors carry the *name* of an env var or a `secret://` handle, never a value (INTERFACES.md:199-205) — same rule as our instinct 5. Honest non-goals: not a multi-tenant boundary; a model capable of routing around its own policy is acknowledged, not solved.

## 4. What to steal for Limpet — concrete mappings

Limpet today (docs-site/docs/architecture.md): five components — HAL (udev USB inventory), MCP meta-server (one endpoint, trust gate), Registry (validates plugins/tools), LLM gateway (litellm), Agent core (owns the tool loop, trust boundary). The mappings, strongest first:

1. **Policy formed at arrival → the MCP meta-server's trust gate.** Today the gate is a static allow decision at tool-registration time. Octopus's move: recompute the effective grant per *call* from (agent role ceiling) ∩ (workspace/task scope) ∩ (session bindings), and make the gate's answer a pure function of files on disk. Nothing an agent session accumulates becomes a right; a hijacked session can't escalate because there is no stored grant to mutate. Cheap to adopt — Limpet already has the registry as the descriptor store.
2. **Worker = two JSON files → Limpet agent/service definitions.** Replace imperative agent setup with pin + ceiling folders: `agents/<name>/{agent.json, policy.json}`. A new RE persona (firmware-triage vs. ghidra-driver vs. flasher) is a copied folder with edited tool/device lists. Role prompt as a bound artifact, not a field — lets the same ceiling wear different role texts per task.
3. **Interface identity-is-the-folder → HAL device exposure.** Each attached probe/target is its own interface folder (per-serial, not per-kind), so two identical ST-Links are two identities and a flash job can be bound to exactly one. Classifiers (vendor, protocol) describe but never route. The HAL's hotplug event stream is just an interface that emits artifacts.
4. **Ledger with typed refusals → Limpet audit layer.** One append-only JSONL of every tool call, device operation, *and every refusal with a closed reason vocabulary*. This is the piece that makes "why didn't the agent flash the board" answerable from disk. The two-layer index discipline (in-memory for gate decisions, disposable SQLite for the TUI/queries) transfers verbatim and matches how we already treat claude_memory vs. PostgreSQL.
5. **EE nesting → sandbox/permission model for RE tasks.** Session root (`existence` ≈ workstation session) → project EE (this device) → task EE (this dump), each narrower: which tools, which device interfaces, which filesystem roots. "Boundary can only narrow" gives untrusted-firmware handling for free: the EE that reads extracted firmware simply never contains flash-write or network grants — exactly our downloaded-files-are-untrusted doctrine, enforced structurally.
6. **Run-as-data → long RE campaigns.** A multi-day porting effort parked on "waiting for Frank to attach the UART adapter" should be a JSON record with an unmet checkpoint bound to a HAL hotplug interface, not a tmux session or a busy-key. Survives reboot by construction; the lumen_busy protocol is a hand-rolled approximation of this.
7. **Marks/verifiers → our evidence gate, generalized.** The foreman's acceptance.json + harness re-run is already "mark recorded only after verifier passes." Octopus adds: verifiers as removable per-check script packages, params authored with the task, verdict on the artifact edge. Worth folding back into foreman as much as into Limpet.
8. **Self-extension → the workstation that adapts to the RE task.** Scenario 1 is literally our use case with the nouns changed: agent needs to speak a new debug protocol → authors a contract for a coder run → coder drops a new driver folder + descriptor into the registry → health checks validate → capability available to later tasks without rebuild. Limpet's plugin registry already validates and health-checks plugins; what's missing is only the authoring path being an ordinary gated agent operation.

**Port-relevant facts:** kernel is platform-independent Rust except the sandbox backend (macOS `sandbox-exec`; the docs state enforced mode refuses to start on platforms without an equivalent — Linux port needs a landlock/bwrap/namespaces backend, which Limpet already has opinions on). Provider layer is swappable by design (OpenAI-compatible driver exists; DeepSeek V4 via LiteLLM is a descriptor, not code). The ChatGPT dependency is confined to one interface folder, as advertised.

## 5. What NOT to copy

- **The all-JSON-descriptor maximalism.** Thousands of descriptor folders is the right call for a self-modifying research OS; for Limpet it's over-generalized. Steal the *fail-closed loading* ("a malformed descriptor is rejected, never defaulted") and the no-branch-on-names rule; keep our flatter registry.
- **No-deadlock-detector purism.** True only because a human owns the client. A fleet of headless workers needs the watchdog layer Octopus proudly omits (we learned this; see wq4629).
- **One commit, one provider, zero tests exercised beyond macOS/Codex.** The docs themselves warn "a descriptor is not a test" and that GPT-6-class models don't actually use the self-evolution affordances unprompted (architecture.md:619-635). Treat every claim above as *design* evidence, not operational evidence.
- **Research-preview state handling:** incompatible format = fresh Home, no migrations. Fine for them, not for a workstation holding weeks of RE state.

## 6. Bottom line

Octopus-SI-OS's real contribution is a demonstration that an agent OS needs no privileged subsystems: no scheduler, no context builder, no memory manager, no recovery module, no inter-agent router — each is an artifact crossing an interface under a grant formed fresh from disk. For Limpet, adopt in order: (1) per-call grant formation at the MCP gate, (2) declarative worker pins, (3) the typed-refusal ledger, (4) EE-style narrowing for untrusted-firmware tasks. Items 1–3 are incremental changes to components Limpet already has.
