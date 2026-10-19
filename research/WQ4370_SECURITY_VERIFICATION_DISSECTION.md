# wq#4370 — LIMPET RESEARCH: dissect security verification + per-tool confinement

Four tools from the ticket, dissected against primary sources:

1. **Cisco DefenseClaw** — unified MCP integrity verification + skill scanning + AI BoM.
2. **Sandlock** — per-invocation confinement via Landlock + seccomp-bpf (not, as the
   ticket frames it, "+ user namespaces" — see §2, this matters for Limpet).
3. **AgentBox** (part of the **AgentBound** framework) — manifest-gated zero-privilege
   container per MCP server.
4. **GBase Lifeline** — automatic git pre-snapshot before every agent code edit.

**Sourcing note (instinct 8 — anti-fabrication):** all four names postdate the model
cutoff, so every claim below came from `fleet-search web`/`read` on 2026-10-19, not
memory. Three of the four have an identifiable **primary source** I read directly:
Sandlock is a real arXiv paper (**2605.26298**, Wang & Zheng, submitted 2026-05-25,
code at `github.com/multikernel/sandlock`) fetched via its HTML rendering
(`arxiv.org/html/2605.26298v1`). AgentBox is the enforcement engine inside **AgentBound**
(arXiv **2510.21236**, Bühler et al., v3 2026-04-24), fetched the same way. GBase Lifeline
is a real, small, individually-authored project (`github.com/nora9217/gbase`, forked from
`garyqlin/gbase`, "Gary Lin, 2026" — a personal RSI-agent-framework project, not an
enterprise tool), fetched via its README. **Cisco DefenseClaw has no primary doc I could
reach** — the ticket's own framing (unified MCP+skill+BoM verification, NVIDIA OpenShell
integration) traces almost verbatim to a **secondary source**, the OWASP AISVS
project's research chapter `C09-03-Tool-and-Plugin-Isolation.md`
(`github.com/OWASP/AISVS`), corroborated by a Chinese-language trade write-up
(`ithome.com.tw/news/174676`) of the RSA Conference 2026 announcement. Where the OWASP
secondary source's numbers conflict with a primary paper I could read directly (Sandlock),
I flag the conflict below rather than silently repeating the more dramatic figure.

---

## 1. Cisco DefenseClaw — unified verification + AI BoM

**What it is:** announced at RSA Conference, March 2026. An open-source framework that
automates the security-verification pipeline for **OpenClaw**-style agents, built on top
of **NVIDIA OpenShell** (announced at NVIDIA's GTC, earlier in 2026) — OpenShell supplies
the infrastructure-level sandbox primitives (kernel isolation, default-deny network,
mandatory policy enforcement, a "privacy router"); DefenseClaw layers verification on top:

- **Skills Scanner** — scans and sandboxes agent *skills* (reusable instruction+code
  bundles, the same concept NanoClaw's own ecosystem uses — see `research/WQ4348_...`).
- **MCP Scanner** — verifies MCP *server* integrity (presumably signature/hash-based
  before use; neither source gives the exact mechanism).
- **AI BoM** — automatic inventory of all AI assets in use (models, skills, MCP servers,
  agents) — a bill-of-materials for the agent's own supply chain, not just its dependency
  tree.
- **CodeGuard** — named but undetailed in both sources.
- Per the Chinese trade coverage: adds **pre-install scanning, runtime threat detection,
  and enforced blocking** on top of OpenShell's sandbox, and an integration plan to push
  verification from dev-time into production runtime.

**What's actually confirmed vs. inferred:** "first vendor-backed open-source framework
that treats MCP server verification and agent skill scanning as a unified security
problem" is the OWASP doc's own characterization, not an independently-verified fact —
treat the "first" claim as marketing framing, not dissected mechanism. No source gives
scan internals (static analysis? LLM-based skill review? signature registry?) — this is
a name-and-shape-level description, not an implementation to copy from.

## 2. Sandlock — per-tool confinement, and a correction to the ticket's framing

**What it is, per the primary paper (arXiv 2605.26298):** a lightweight Linux process
sandbox for AI-agent workloads (shell commands, downloaded scripts, MCP-invoked tool
calls) that explicitly rejects the container/microVM model as too heavyweight for a
workload that invokes untrusted code "dozens of times per minute." Design split:
**static, input-independent policy is compiled into kernel-enforced rules (Landlock)**;
**a narrow userspace supervisor handles runtime-dependent decisions** (via seccomp user
notification, not user namespaces).

**Correction — the ticket's "Landlock + seccomp-bpf + user namespaces" is wrong on the
third term.** The paper's own abstract states the whole point of the design is enforcing
"filesystem, network, IPC, and syscall policies **without root, cgroups, images, or
mandatory namespaces**." User namespaces are not Sandlock's third pillar; Section 4
names exactly two: **Landlock as the static spine** (filesystem/TCP-port/IPC rules,
requires **Landlock ABI 6+, i.e. Linux 6.12+** — confirms the ticket's kernel-version
claim) and **seccomp-bpf as the filter**, with a **seccomp user-notification supervisor**
(an async tokio task) handling the dynamic cases a static rule can't resolve (dynamic
network allow/deny, HTTP-method/host/path-level ACLs, TOCTOU-safe `execve` argument
inspection, copy-on-write filesystem capture for commit/abort/dry-run). The three-term
"Landlock+seccomp+namespaces" phrasing appears to come from the OWASP secondary source,
which itself gets this wrong; the primary paper reads as deliberately *avoiding*
namespaces as a mandatory dependency, since mandatory namespaces are exactly the kind of
setup cost (provisioning, `/etc/subuid`, rootless-Docker-style config) the paper frames
containers' namespace machinery as imposing on a hundreds-of-milliseconds-sensitive
workload.

**Correction — the ticket's performance numbers are also inflated versus the primary
source.** The ticket (echoing the OWASP doc) says "sub-millisecond overhead... 1000x
faster than container startup." The paper's own abstract and evaluation section say
**"Sandlock adds roughly 5 ms of startup overhead... 6 ms wall, 44× faster end-to-end
than Docker on our setup," and "no measurable Redis throughput overhead."** 5 ms is not
sub-millisecond, and 44× is not 1000×. Both numbers are real engineering wins (one
order of magnitude below "hundreds of milliseconds," the number the paper gives for
typical container startup) — but Limpet should plan around the paper's own 5 ms/44×
figures, not the secondary source's rounder, more dramatic ones.

**Mechanism detail worth keeping:** a **seccomp-bpf deny set** is "conservative by
default," covering only syscalls with no Landlock equivalent, since Landlock already
covers filesystem/network/IPC. The supervisor's syscall table (Table 2) specifically
intercepts `clone`/`fork`/`vfork`/`clone3` for **process-count enforcement**,
`mmap`/`munmap`/`brk` for memory accounting, `connect`/`send*` for **IP allowlist + HTTP
redirect**, `openat`/`unlinkat`/`mkdirat`/`renameat2` for **copy-on-write write
capture**, and `exec*` for a `policy_fn` hold. Sandlock refuses to run below the Landlock
ABI floor rather than silently downgrading — same fail-closed posture Limpet's own build
sandbox already takes on egress (D3 below). Its **pipeline operator** supports
**per-stage confinement** — splitting one agent task into differently-confined stages
(e.g., the stage that reads untrusted content has no network; the stage with network
has no untrusted-content exposure) — a direct, named mitigation for Willison's "lethal
trifecta" (private-data access + untrusted-content exposure + external communication in
one trust domain), cited explicitly in the paper's threat model.

## 3. AgentBox (inside AgentBound) — manifest-gated MCP isolation

**What it is, per the primary paper (arXiv 2510.21236, "AgentBound: Securing Execution
Boundaries of AI Agents"):** the first access-control framework for MCP servers,
combining a **declarative policy mechanism modeled on the Android permission model**
(`AgentManifest`) with a **policy enforcement engine** (`AgentBox`) that contains
malicious server behavior **without requiring MCP server source modification** — servers
are wrapped transparently inside a container.

- **Zero-privilege-by-default, confirmed in the paper's own text:** "By default, servers
  start with no privileges; only generic capabilities explicitly specified in the
  manifest can be instantiated as runtime permissions at execution time." Granular
  capability examples given: a filesystem-access server gets a **read-only mount**; a
  server needing outbound calls gets a **domain-restricted allowlist**, not open egress.
  (This is the same "a manifest claim must cash out as a narrower grant, not a broader
  one" posture as Sandlock's HTTP-method/host/path ACLs and NanoClaw's mount allowlist —
  three independent projects converging on the same shape.)
  Code: the paper's own worked attack example is a poisoned-tool-description prompt
  injection telling the LLM to overwrite a read file and exfiltrate its contents via a
  second MCP server's fetch tool; AgentBox blocks it because the filesystem server's
  mount is read-only and the fetch server's domain allowlist excludes the exfil host —
  confinement holds even though the *model* fully complied with the injected instruction.
- **Manifests are auto-generated, not hand-written, and imperfect:** the paper's own
  tool (`AgentManifestGen`) generates manifests from source code at **80.9% out-of-the-
  box accuracy** across a 296-server dataset, with "strict output validation that rejects
  drafts that deviate from the finite capability vocabulary or omit rationales." 80.9% is
  high but not complete — roughly one in five auto-generated manifests needs a human
  correction before it's trustworthy as a security boundary.
- **The ticket's "user confirmation layered on manifest, prevents malicious manifest
  escalation" claim is not literally in the paper.** The paper describes MCP's standard
  Host role as the thing that "enforce[s] security and consent policies" (generic MCP
  architecture, not an AgentBox-specific feature), and discusses manifest *correctness*
  (auto-generation accuracy) rather than manifest *tamper/escalation* detection — no
  signature, hash, or integrity-check mechanism for the manifest file itself is described.
  Treat "prevents malicious manifest escalation" as the ticket's own gloss on "the
  enforcement layer is independent of what the manifest claims," not a cited AgentBox
  feature — if Limpet wants tamper-resistance, that's a gap to design for, not a thing
  to assume AgentBox already closed.

## 4. GBase Lifeline — pre-mutation git snapshot

**What it is:** a feature of `GBase` ("Genius Base"), a real but small/individually-
authored "recursive self-improvement" agent framework (`github.com/nora9217/gbase`,
originally `garyqlin/gbase`, "Gary Lin, 2026, personal project," MIT, Python). Per its own
README feature table: **"🚨 Lifeline — Automatic git pre-snapshots before every code
modification. 'Undo' is always one command away."** That one line is the entirety of the
documentation reachable this run — no README section details commit granularity (one
snapshot per file write? per tool call? per task?), storage location, or retention/pruning
policy.

**The ticket's "multi-armed review gate (build/audit/judge)" is a separate GBase
feature, not part of Lifeline itself**, confirmed in the same README: **"🛑 Quality
Gates — Multi-armed review pipelines: one agent builds, another audits, a third judges.
Code that ships is code that survives cross-examination."** Worth noting for Limpet: this
is structurally the same pattern as Limpet's own `judge`/`harness` split already visible
in this worktree (`judge/`, `harness/fworker.py` evidence-gate verification) — GBase names
it explicitly as a reusable three-role pattern (build/audit/judge) rather than two
(build/judge), which is the one concrete idea worth lifting, independent of Lifeline.

**Caveat on provenance (same posture as the NanoClaw doc's fork-sourcing caveat):** this
is a solo hobby project with a cyberpunk-themed web UI and four "editions"
(Hacker/Prime/Standard/Lite) — credible as a *pattern* (pre-mutation snapshotting is a
well-understood idea independent of this specific repo), not as a *tool* Limpet should
take a dependency on. Nothing here should be read as "GBase is a vetted, production-grade
framework" — it's evidence that the snapshot-before-mutation pattern exists in the wild
under this name, nothing more.

---

## Recommendations for Limpet

Limpet already has a fail-closed sandboxing precedent to build on: **D3 — the build
sandbox has no egress** (`limpet/DESIGN-DECISIONS.md:106`, wq#2595/wq#3997) — fetches are
refused by default and downloaded exploit bodies stay untrusted, never auto-run
(`limpet/DESIGN-DECISIONS.md:110`). All four recommendations below extend that existing
fail-closed posture rather than introducing a new one.

- **A. Per-tool confinement: adopt Sandlock's shape, not its exact numbers.** For any
  Limpet skill that executes untrusted code picked up during RE work (a QEMU guest, an
  extracted-firmware binary, a generated patch script), Sandlock's two-layer design
  (Landlock static rules + a narrow seccomp-notification supervisor for the dynamic
  cases) is the right-sized primitive for Limpet's actual workload — RE tasks that invoke
  untrusted binaries far more often than they need a fresh container. Plan for **Linux
  6.12+ (Landlock ABI 6)** as a hard floor — Sandlock itself refuses to run below it rather
  than downgrading silently, which is the correct failure mode to copy. Do **not** plan
  around "sub-millisecond"/"1000×" marketing numbers; plan around the paper's own
  **~5 ms startup / 44× vs. Docker** — still a decisive win over per-invocation container
  spin-up for a workload that runs untrusted code "dozens of times per minute," just not
  the number the ticket quotes.
- **B. AI BoM: scope it to what Limpet actually accumulates.** DefenseClaw's AI BoM
  concept (automatic inventory of models/skills/MCP servers/agents in use) maps directly
  onto Limpet's own KB catalog pattern already shipped in `limpet/kb.py` — a per-source
  TOML entry (`limpet/kb-sources.d/*.toml`) with name/category/fetch-command/staleness
  marker is structurally a BoM row. Extending that same catalog to also track **which RE
  skills and MCP tool servers a given Limpet run actually invoked** (not just which KB
  sources it read) gets most of DefenseClaw's AI-BoM value using machinery Limpet already
  has, without taking a dependency on an external, undetailed scanner.
- **C. MCP server isolation policy: adopt AgentBox's manifest shape, but add
  integrity-checking it doesn't have.** For any MCP server Limpet wires in (Rockchip
  tooling, bench-tool catalog entries — `limpet/DESIGN-DECISIONS.md:157`), require a
  manifest declaring the narrowest capability set the server needs (read-only mount for
  filesystem servers, domain-allowlisted egress for fetch-capable servers) and enforce it
  independently of the server's own claims — AgentBox's own worked example shows
  enforcement holding even when the *model* is fully compromised by a prompt injection,
  because the boundary is a mount/allowlist, not a prompt. Because the paper does **not**
  describe a manifest-tamper or signature check, Limpet's own version should add one
  (hash-pin or sign the manifest file itself, verified before the server is wrapped) —
  this is a gap AgentBox leaves, not a feature to assume it already covers.
- **D. Snapshot strategy for file-mutating hunters: cheap pre-mutation git snapshots,
  commit granularity per mutating action.** GBase Lifeline's one-line pitch ("undo is
  always one command away") is the right *shape* for any Limpet skill that patches a
  binary, re-flashes an image, or edits an extracted filesystem in place — a lightweight
  `git commit`/`git stash`-equivalent snapshot taken immediately before the mutating call,
  not a periodic cron snapshot. Since GBase's own docs don't specify granularity, Limpet
  should pick **one snapshot per mutating tool invocation** (not per task, not time-based)
  so "undo" always rewinds exactly one action — matching the existing fleet-wide worktree
  discipline this very ticket runs under (never `git stash`/`reset --hard` without
  capturing state first). Pair it with GBase's separately-named **build/audit/judge**
  three-role review gate as the review step *after* a mutation, structurally extending
  Limpet's existing judge/harness evidence-gate split (`harness/fworker.py`) from two
  roles to three for any skill that both writes files and claims success.

## Anti-patterns to avoid

1. **Repeating a secondary source's numbers as if they were the primary source's.** The
   OWASP AISVS doc's "sub-millisecond, 1000×, Landlock+seccomp+namespaces" framing of
   Sandlock doesn't match the paper it's citing. Don't let Limpet's own sandboxing design
   doc inherit a compressed/rounded claim two hops removed from the actual engineering
   result — cite the paper, not the citation of the paper.
2. **Treating "manifest-gated" as "tamper-proof."** AgentBox enforces what a manifest
   declares; neither source shows it verifying the manifest hasn't been swapped or edited.
   A Limpet MCP-isolation policy that trusts the manifest file itself without integrity-
   checking it reintroduces the exact escalation path the ticket assumed was already shut.
3. **Taking a hard dependency on a hobby project for a security property.** GBase is a
   solo/personal-project framework with no visible production track record. The pre-
   mutation-snapshot *pattern* is worth adopting; the specific repo is not worth depending
   on — implement Limpet's own thin git-snapshot wrapper rather than importing GBase.
4. **Downgrading silently instead of refusing.** Both DefenseClaw's and Sandlock's
   designs (ABI-floor check, fail-closed on missing kernel feature) treat "can't verify
   the strong guarantee" as "refuse to run," not "fall back to a weaker one quietly." Any
   Limpet confinement wrapper built on Sandlock-style primitives should fail the same way
   if the host kernel is below 6.12, rather than silently running unconfined.
