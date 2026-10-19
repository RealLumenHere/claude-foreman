# wq#4367 — LIMPET RESEARCH: dissect control-plane orchestrators

Three live, sourced projects that each solve one piece of the orchestration problem
Limpet's agent core (`design/wq3806_limpet_architecture.md` §6, lines 258-309) still has
open: **OpenAI Symphony** (ticket-as-state-machine, objectives-not-transitions),
**Captain Claw** (plan mode with per-step verification and bounded revision), and
**sheath** (references-not-payloads context offloading, the most concrete of the three).

**Sourcing note (instinct 8 — anti-fabrication):** all three postdate the model cutoff, so
every claim below came from `fleet-search web`/`read` on 2026-10-19 (SearXNG's own engines
were suspended/CAPTCHA'd this run — brave/duckduckgo/google-cse/startpage all reported
`Suspended`/`CAPTCHA`; the keyed DeepSeek-Search fallback carried web discovery), plus two
primary sources fetched directly: OpenAI's own blog post
(`openai.com/index/open-source-codex-orchestration-symphony`, full text captured) and
sheath's PyPI JSON metadata API (`pypi.org/pypi/sheath/json`, which returns the package's
full `README.md` as plain text — the HTML project page itself required JavaScript and
returned nothing useful). Captain Claw's README came from a direct fetch of
`github.com/kstevica/captain-claw`. `fleet-search github` (structured GitHub API search)
was unavailable this run — `github 401 — no usable token` — so repo file contents below
are read from the two READMEs' own file-layout tables, not independently confirmed by
browsing the trees; treat exact line numbers inside *their* source files as table entries
they published, not something I opened directly.

---

## 1. OpenAI Symphony — ticket-as-state-machine, objectives not transitions

**What it is:** an open-source *specification* (`SPEC.md`, reference implementation in
Elixir — picked for its concurrency/supervision primitives, "because when code is
effectively free, you can finally pick languages for their strengths") that turns a
project-management board (Linear, in OpenAI's own deployment) into a control plane for
coding agents. Released ~April 2026; ~15K GitHub stars as of April 23 per the post's own
"Community shoutouts" line. Built by Alex Kotliarskyi, Victor Zhu, and Zach Brock at
OpenAI, running on top of Codex's headless **App Server** mode (JSON-RPC, not CLI/tmux).

- **Every open ticket gets a dedicated agent workspace.** Symphony continuously watches
  the board; every active task has an agent running in the loop until done. A crashed or
  stalled agent is restarted; new work is picked up automatically. The engineers' own
  framing: *"any open task should get picked up and completed by an agent... we made our
  issue tracker the control plane."*
- **Ticket status as state machine.** Linear's own statuses *are* the state machine
  Symphony drives against — not a parallel state store Symphony owns.
- **Objectives-not-transitions (the ticket's own framing, verbatim from the post).**
  Symphony's *first* version treated agents as rigid state-machine nodes — Codex was only
  ever asked to "implement the task." That broke down as models got more capable: *"Codex
  is perfectly capable of creating multiple PRs as well as reading review feedback and
  addressing it... these types of tasks fell way outside the initial feature implementation
  box."* The fix was to **stop hard-coding the transition sequence** and instead give the
  agent tools (`gh` CLI, CI-log-reading skills) plus an objective, "much like a good manager
  would assign a goal to a direct report." This is the single most load-bearing lesson in
  the whole post, called out as its own section ("Progress comes with new, different
  problems").
- **DAG emerges from blocking, not from an explicit scheduler.** A large task becomes a
  tree of sub-tasks with declared dependencies; *"agents only start working on tasks that
  aren't blocked, so execution unfolds naturally and optimally in parallel for this DAG."*
  Their own worked example: a React-upgrade ticket marked blocked-on a Vite migration only
  started once the migration ticket closed — ordinary ticket-dependency semantics, not a
  bespoke DAG engine.
- **Agents create their own work.** Mid-task, an agent that notices an unrelated
  improvement (perf issue, refactor opportunity) files a new ticket for later triage rather
  than scope-creeping the current one — keeping the current task's objective stable while
  still capturing the finding.
- **Failure handling is orchestration, not agent-prompt text.** For large monorepos,
  Symphony "watches CI, rebases when needed, resolves conflicts, retries flaky checks, and
  generally shepherds changes through the pipeline" — by the time a ticket reaches
  "Merging," there's high confidence it lands without human babysitting. This is
  infrastructure the *orchestrator* owns, independent of what any one agent decided to do.
- **Credential isolation via dynamic tool calls, not MCP.** To avoid handing the Linear
  access token to every subagent, Symphony wraps a raw `linear_graphql` function behind
  Codex's dynamic-tool-call mechanism rather than exposing a Linear MCP server (and its
  token) directly to containers — the same shape as NanoClaw's OneCLI vault
  (`research/WQ4348_NANOCLAW_DISSECTION.md` §3): the agent gets a capability, never the raw
  secret.
- **Deliberately minimal, not a maintained product.** OpenAI states outright they will not
  maintain Symphony as a standalone product; the artifact is `SPEC.md` plus one Elixir
  reference implementation, and they explicitly reimplemented it in TypeScript/Go/Rust/
  Java/Python with Codex itself "to identify ambiguities and simplify the system" before
  calling the spec done. The intended consumption pattern is: point your own coding agent
  at the spec and have it build your version.
- **Measured impact, OpenAI's own numbers:** 500% increase in landed PRs on some internal
  teams in the first three weeks; Linear's founder separately noted a spike in new
  workspaces created coinciding with the release (cited in the post, not independently
  verified by me beyond the post's own citation).

---

## 2. Captain Claw — plan mode, per-step acceptance, bounded revision

**What it is:** `kstevica/captain-claw` — "Flight Deck," a self-hosted, MIT-licensed,
model-agnostic multi-agent orchestration framework (OpenAI/Anthropic/Gemini/DeepSeek/
Ollama/OpenRouter, or fully local). Ships as a PyPI package (`captain-claw`, versions
0.4.x–0.7.x+ observed in search results) plus the Flight Deck web UI. Six orchestration
modes, 44-51 built-in tools per agent (count varies by version in the snippets found), six
shared memory layers.

- **Plan Mode — the per-step acceptance / bounded-revision pattern the ticket names.**
  Quoting the README's own description of the feature: *"A new toggle decomposes a task
  into ordered steps, drives each to a verified result before the next, re-plans the
  remainder when a step can't be verified, and synthesizes from the verified steps — so the
  system never compounds an unverified step."* Each step can run as a single fast model, a
  full "Basna" ensemble (N agents blind-answer the same question, merged by reliability), or
  a full "Vatra" team (shared-blackboard collaboration) — chosen by the mode card. Ticking
  "parallel" makes the planner emit a dependency graph so independent steps run in
  concurrent waves, rather than always serializing. Code: `captain_claw/session_orchestrator.py`
  (per the README's own component table) is named as the DAG orchestrator.
- **Orchestrate/DAG mode — the explicit fan-out mode.** `/orchestrate <prompt>`: the LLM
  decomposes the prompt into a task DAG with dependencies, runs it with a configurable
  parallel-worker count over a shared workspace (for inter-task data flow), validates
  structured output against a **JSON Schema with auto-retry**, and exposes a headless CLI
  entry point (`captain-claw-orchestrate`) for cron/scripts — plus a Gantt-style trace
  timeline for after-the-fact inspection.
- **Independent, parallel, two-stage-ish review on the coding pipeline ("Code").** After
  every build, three reviewers run in parallel — correctness, a CVSS-ranked security pass,
  and a QA pass that *actually executes the test suite* (not just reads the diff). A triage
  judge then decides ship-or-fix, but **only acts on blocking/major findings** — minor
  findings don't trigger a fix cycle. A capped fix loop applies precise instructions; from
  round 2 onward, review is a *delta* review of just the fix diff, not a full re-read. At
  the cap, remaining open findings persist to a backlog rather than being dropped, and a
  later "continue fixing" resumes exactly there.
- **Sourcing caveat — the two specific numbers in the ticket's framing are not independently
  confirmed.** The ticket text says "default 2 revisions" and "two-stage verification: JSON
  schema then LLM judge." What I can confirm from the README: Orchestrate mode does JSON
  Schema validation with auto-retry (a schema-level gate), and the Code pipeline's fix loop
  is explicitly *capped* with delta-review rounds and a backlog fallback (a judge-level
  gate, bounded, not unbounded). I could not find the literal default-revision-count
  constant or an explicit "schema first, then judge" sequencing statement for *Plan Mode*
  specifically in the README text captured — that level of detail likely lives in
  `USAGE.md`/`RELEASE_NOTES_0.6.4.md` (both referenced by the README but not fetched this
  run). Treat "schema-then-judge, bounded revisions" as the pattern **Orchestrate + Code
  together demonstrate**, not a verbatim Plan Mode spec line.
- **Bat mode (0.8.0) — a related, more autonomous finisher pattern worth noting.** "A
  durable 'stubborn finisher': plans, runs checkpointed steps with retries, survives
  restarts, and stops only when an independent judge says the task is done — behind a hard
  safety floor and a start gate for email, spending and accounts." This is Plan Mode's
  verified-step idea taken further: no step budget at all, just a judge-decided stop
  condition plus a hardcoded safety floor for irreversible-action categories — structurally
  identical to Limpet's own energize-confirm boundary
  (`design/wq3806_limpet_architecture.md` lines 294-302), which is also "autonomy for
  planning, a hard operator gate for the irreversible action regardless of trust or prior
  success."
- **Autonomous Work loop — notice → decide → act → judge → learn.** An "arbiter" picks one
  action per heartbeat; a Bayesian reliability weight learns from every outcome; a *trust
  ladder* auto-promotes a reversible tool action to auto-fire once it's earned enough
  successful outcomes. Plans in this mode follow "decompose-execute-verify, with
  replan-on-failure," and verification is "grounded" — the result is checked by reading the
  actual side effect back, not by trusting the action's own report of success. That last
  point is the same posture sheath takes with handle metadata (§3 below) and that Limpet's
  `critic.py` propose/critic/adjudicate step already takes with findings
  (`design/wq3806_limpet_architecture.md` lines 281-289) — re-derive truth from the
  artifact, never believe the actor's self-report.

---

## 3. sheath — references, not payloads (the concrete context-offloading implementation)

**What it is:** `jspv/sheath` (PyPI `sheath`, v0.2.0 at time of research) — "a reusable
Python substrate for building autonomous data-gathering and data-integration agents," a
single-agent-loop harness built on the **Microsoft Agent Framework** (MAF). Of the three
projects in this ticket, sheath is the only one with its full README captured directly
(via PyPI's JSON metadata API, which serves the package description verbatim — the
HTML project page requires JavaScript and returned nothing), so citations below are
file:line-equivalent (file + README section), not secondhand summary.

- **The core move, in the project's own words:** *"Large or structured returns from tools,
  MCP servers, and URLs spill to typed handles (JSON / text / Parquet) before they reach
  the model — ~200 tokens stand in for megabytes."* A "spill" middleware
  (`sheath/spill.py`, wired as a MAF `result_parser`) intercepts any tool/MCP/URL return
  over `spill_threshold_bytes` (default **8192 bytes**) and writes it to disk as a typed
  handle instead of letting it reach the model. The model sees exactly this shape (verbatim
  from the README's own worked example):
  ```json
  { "handle": "h1", "kind": "dataframe", "source": "query_sales(region='EU')",
    "schema": {"date": "date", "units": "int", "revenue": "float"},
    "n_rows": 480000, "n_cols": 3, "bytes": 18402211,
    "path": "handles/h1.parquet",
    "preview": "date,units,revenue\n2025-01-01,12,840.0\n... (5 of 480000 rows)" }
  ```
  — schema + row/byte count + a truncated preview, ~200 tokens standing in for 18 MB. A
  return larger than `max_spill_bytes` (default 100 MiB) is **rejected loudly**
  (`SpillLimitExceeded`), never silently stored.
- **Sandboxed `run_python` speaks the same handle store.** The agent's only way to actually
  work with spilled data is to write and run a script that calls `load(id)` / `save(id,
  obj)` against the same store; only what the script deliberately prints/returns flows back
  into context, and anything it derives becomes a new handle rather than inline text. The
  README names the loop explicitly: **search → read → analyze** (`search` to locate,
  `read_file` to load the right slice, `run_python` to analyze).
- **`safe_path` — the single chokepoint, by the project's own description "the most
  heavily tested code in the project."** Every model-supplied path (`sheath/paths.py`,
  per the project-layout table) resolves symlinks *before* checking and rejects anything
  that lands outside the session root — blocking `..`, absolute-path escapes, and
  symlink-based escapes in one place. Nothing else in the security model matters if this
  one function is wrong, which is exactly why the README calls it out by name rather than
  listing it as one bullet among many.
- **Workspace trust zones (`sheath/workspace.py`).** Four harness-owned directories
  (`handles/`, `inputs/`, `.scripts/`, `.sheath/`) are mounted **read-only** into the
  sandbox; sandboxed code gets exactly one writable per-run exchange directory
  (`/sheath-run`). The harness never trusts what it reads back from that exchange
  directory either: fixed file names, no-follow opens, regular-file-only, size-capped, then
  the harness itself *moves* the payload into `handles/` — the sandboxed process never
  writes directly into the directory the model will later be told to trust. An **orphan
  quarantine** state (`SandboxQuarantinedError`) triggers if a container-tier run times out
  and the runtime can't confirm the container was actually removed — every tool call
  refuses until a forced `rm -f` on that exact container name is confirmed, rather than
  risking a zombie process still writing into the root.
- **Sandbox tiers — container is the default, not an opt-in.** `run_python` runs in a
  hardened OCI container (Podman preferred, Docker supported) by default: network off,
  read-only root filesystem, `--cap-drop ALL`, non-root, memory/cpu/pid limits. The `local`
  tier (bare subprocess, resource rlimits + wall-clock timeout only, **no isolation** — "the
  code runs as the host user") is an explicit, warned opt-out
  (`NoSandboxIsolationWarning`), and `SandboxConfig.require_isolation=True` refuses to let
  the local tier run *however it was selected* (explicit config or env var), raising
  `SandboxRuntimeUnavailable` instead of silently degrading. Planned (not yet shipped, per
  the README's own Roadmap section): a **micro-VM tier** (gVisor/Firecracker) behind the
  same `SandboxExecutor` interface — the same container→microVM escalation path NanoClaw
  already shipped (`research/WQ4348_NANOCLAW_DISSECTION.md` §2).
- **Egress is filtered at the harness, not left to the sandbox.** `fetch_url` /
  `read_document(url)` deny loopback/private/link-local/reserved/multicast/unspecified
  ranges by default, *plus* `100.64.0.0/10` (carrier-grade NAT — called out explicitly
  because no standard `ipaddress` library flag reports it as private). Redirects are
  re-validated on every hop (the README is explicit that `follow_redirects=False` is
  forced on the underlying HTTP client specifically so the harness's own redirect-following
  code re-checks each hop against policy). **Cloud metadata endpoints cannot be
  allowlisted at all** — `169.254.169.254`, `169.254.170.2`, `168.63.129.16` (Azure's
  wireserver — flagged as notable because it's otherwise a *public* address nothing else
  would block), `100.100.100.200`, `192.0.0.192`, `fd00:ec2::254` are denied unconditionally,
  distinct from the regular private-range denylist which *can* be selectively reopened via
  `allow_private_hosts` for legitimate internal data sources.
- **Handle integrity — don't believe the sandbox's self-report.** Handle metadata (schema,
  row count, preview) is derived by the harness from the bytes on disk *when the handle is
  created*, never from what the sandboxed code claims about what it wrote — and it is
  *re-derived*, not trusted from a cached record, whenever a manifest is rehydrated. In the
  container tier the handle bytes themselves are immutable (read-only mount); as
  defense-in-depth even in the `local` tier, a content digest (sha256 over length + first
  64 KiB + last 64 KiB — explicitly not a whole-file hash) is checked on every read,
  raising `HandleTamperedError` on mismatch.
- **Honestly documented residual risk, worth copying the posture even without copying the
  code:** the README's own "Known residual" callouts — DNS-rebinding between
  validation-time and connect-time resolution is not closed; the fetch deadline doesn't
  cover DNS resolution; container-tier disk usage is unbounded. These are written in the
  README itself, not discovered by me — the project documents what its own boundary does
  *not* cover, the same practice Limpet's own DESIGN-DECISIONS.md "Known remaining gap" /
  "Scope held back" sections already follow (e.g. wq#4127's open gap in `sanitize.py`,
  line 32-34 of that file's log entry above).

---

## Orchestration pattern recommendation for Limpet: hybrid, not a pick-one

Limpet is not a fleet of parallel tickets pulled off an external board — it is one
appliance running one RE investigation per workspace session
(`design/wq3806_limpet_architecture.md` §5-§6). That shape rules out adopting Symphony's
*literal* mechanism (a Linear-like board driving N concurrently-running agent workspaces) —
there is no multi-ticket backlog to pull from inside a single investigation. But Symphony's
**lesson**, not its mechanism, transfers directly: Limpet's own §6 loop is currently
written as a fixed phase sequence — *"identify → choose toolset for the target class →
orchestrate multi-tool steps (extract → static → emulate/debug → verify) → write evidence →
report"* (`design/wq3806_limpet_architecture.md:271-274`). That is exactly the "rigid node
in a state machine" shape Symphony's own authors say broke down once their agent got
capable enough to do more than one fixed thing per box. Limpet's obligation ledger
(`limpet/obligations.py`, wq#3965) already encodes the *objective* framing for the "when am
I done" question — an investigation isn't done by phase, it's done when every obligation is
resolved or waived. The same move should extend to the *middle* of the loop: let the agent
re-sequence tool use when a mid-investigation result changes what's needed (e.g. a firmware
extraction reveals an unexpected embedded hypervisor and the "static → emulate" plan for
that artifact needs to become "identify this sub-target first"), rather than only having
flexibility at the final obligations gate.

Where a DAG *is* the right model is inside a single investigation's tool-orchestration
step, which is already dependency-shaped and just not expressed as one: "extract" blocks
"static," and once a firmware image contains N embedded binaries, static analysis of each
is independent and fans out — Captain Claw's own framing, *"Tick parallel and the planner
emits a dependency graph so independent steps run in concurrent waves,"* is a direct match
for "N embedded binaries, each gets its own static-analysis subtask once extraction
finishes." Recommend making that dependency structure explicit in whatever ticket builds
out the orchestration step of §6, rather than leaving it implicit in prose.

Captain Claw's per-step verification (*"drives each to a verified result before the next,
re-plans the remainder when a step can't be verified"*) is also not a new idea for
Limpet to adopt wholesale — `limpet/critic.py`'s propose/critic/adjudicate step
(`design/wq3806_limpet_architecture.md:281-289`) is already a two-stage gate: a
deterministic check (cited evidence present, no silently-closed obligation) followed by a
second model call whose job is specifically to falsify the claim. The gap is scope: that
gate currently runs once, at the end, on a *finding*. Generalize it to gate each DAG step
of the orchestration loop (not just the final finding commit), and borrow Captain Claw's
circuit breaker for when a step can't pass — *re-plan the remainder* — instead of either
looping forever or failing the whole investigation on one unverifiable step.

**Recommendation: ticket/obligation-driven at the outer loop (unchanged — this is already
Limpet's shape and is correct for a single-session appliance), DAG-based at the inner
tool-orchestration loop (new — make the extract→static→emulate dependency structure
explicit so independent sub-targets fan out), two-stage verification at every DAG step
(extend `critic.py`'s existing pattern from "gate the final finding" to "gate each step"),
bounded re-plan as the failure path instead of an unbounded retry loop.** This is a hybrid
because each of the three ideas slots into a different layer of the loop Limpet already
has, not because "hybrid" is a safe middle answer — Symphony's board-driven ticket model
and Captain Claw's Orchestrate mode both assume a many-concurrent-agent-workspaces shape
Limpet's single-appliance, single-session design does not have.

## Context offloading spec for RE tools

sheath is the template to build against because Limpet's RE tools already produce exactly
the payload shapes sheath's spill middleware targets: Ghidra decompilation dumps, QEMU/
emulation logs, full string-table extractions, partition-table listings, firmware-diff row
sets. Concretely, for whatever ticket builds this out:

1. **Typed-handle store, modeled on sheath's handle shape.** Any RE tool/MCP-wrapper return
   over a byte threshold (sheath's own default, 8192 bytes, is a reasonable starting point)
   writes to a handle — JSON for structured data (symbol tables, partition tables, a
   firmware-diff row set), text for logs/string dumps, a tabular format for large row sets
   (a Ghidra function list, a diff table) — instead of returning the payload inline. The
   agent sees a ~200-token summary: schema, row/byte count, and a truncated preview, plus
   the handle id — the exact shape quoted in §3 above.
2. **One `safe_path`-equivalent chokepoint for handle/evidence paths.** Limpet already has
   the right instinct here — `binary_scrub`/`injection` are deliberately kept as one source
   of truth for "what counts as an injection" rather than two drifting pattern lists
   (`limpet/DESIGN-DECISIONS.md` wq#4108 D1, lines 38-44). The same single-source-of-truth
   discipline should gate every model-chosen path into a handle store or evidence
   directory: resolve symlinks before checking, reject anything that escapes the workspace
   root, and make it the one function every tool routes through — sheath's own framing,
   "the most heavily tested code in the project," is the right bar for how much scrutiny
   this function should get relative to everything else being built.
3. **Sandboxed analysis code that speaks the same handle store.** Limpet's build sandbox
   already has no egress by design (`limpet/DESIGN-DECISIONS.md` wq#4086 D3, "the build
   sandbox has no egress (fleet-wide, wq#2595)") — that posture is the right shape for a
   `run_python`-equivalent step that loads a decompile/log handle, filters or greps it, and
   returns a small result, without ever putting the full tool output in context. sheath's
   container-tier defaults (network off, read-only rootfs, `--cap-drop ALL`, non-root) are
   the concrete config to copy for that step specifically, as distinct from — and no more
   permissive than — Limpet's existing no-egress build sandbox.
4. **Don't trust a tool's self-reported handle metadata.** The same evidence discipline
   `limpet/critic.py` already applies to findings (refuse an uncited claim) should apply to
   handles: derive schema/size/preview from the bytes a tool actually wrote, not from what
   the tool call claims it wrote, and re-derive rather than cache that metadata whenever a
   workspace is reopened in a later session — sheath's handle-integrity design is the
   reference implementation for exactly this.
