# wq#3806 — LIMPET architecture design

Limpet is the planned "universal RE agent appliance": an M70q Gen 5 (i5-14500, 32GB
DDR5, Intel UHD 770 iGPU) that an operator plugs a device into and points an agent at.
The seed vision lives in `/home/lumen/projects/limpet/VISION.md`; this document turns it
into a buildable architecture: tool-registry manifest, MCP meta-server, plugin lifecycle,
hardware abstraction layer (HAL), workspace isolation, and agent core.

This is a **design-only** ticket. Nothing here installs software, registers an MCP
server, or adopts a tool — those steps inherit the existing fleet gates (see
§0 Constraints). The companion artifact is
`design/limpet/plugin-manifest.schema.json`, a JSON Schema that formalizes the
`limpet-plugin.toml` format sketched in VISION.md so the registry can validate plugins
mechanically.

## 0. Constraints this design must respect (read first)

Three facts on the ground shape every decision below. Ignoring them would be winging it.

1. **Role conflict with wq#3658.** The fleet role source-of-truth,
   `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md:14`, assigns
   Limpet as *"Secondary control/worker. Runs Ollama qwen3.5-9b + Docker workers … NOT
   the primary control plane yet."* It does **not** record an "RE appliance" role, and
   `research/fleet_hardware.json:417-428` lists Limpet as `unprovisioned-unfiled` with
   `ram_gb: null`. This ticket's "dedicated RE appliance" framing is therefore a *role
   change*, not an elaboration of a settled role. The architecture is designed so the RE
   appliance is the primary workload **and** the "secondary control can take over Lumen's
   duties" property from wq#3658's open items (`:107-110`) is preserved — see §6. Filing
   the actual role-change/provisioning ticket is out of scope here; this doc is what that
   ticket should cite.

2. **The cited catalog does not exist.** The ticket points at
   `/home/lumen/projects/limpet/tool-catalog.json`; that path is absent (only `VISION.md`
   is in that directory, verified 2026-10-14). The "147 tools" is therefore a *target*,
   not an enumerated input. This design does **not** fabricate 147 tool entries. It
   defines the manifest schema and registry so the catalog can be populated and validated
   incrementally, and specifies how to seed it (§1.3) from the RE surface that actually
   exists today.

3. **Community plugins and tool adoption inherit the TOOL_REGISTRY gate.** `TOOL_REGISTRY.md`
   put REA (`github.com/morluto/rea` / npm `rea-agents`) on a **SECURITY HOLD**
   (`TOOL_REGISTRY.md:9`) precisely because a 2-week-old package with astroturfed star
   velocity is "the pattern used to pressure agents into auto-installing a new MCP server
   across infrastructure on social proof alone" (`:22-24`). Limpet's headline features —
   "147+ tools" and a "community plugin system" — are that exact attack surface at scale.
   So the plugin lifecycle (§3) is built around a **trusted/untrusted tier split with a
   mandatory go/no-go** that reuses the existing adoption posture, not an open
   auto-installing marketplace. This is a load-bearing requirement, not a footnote.

## 1. Tool registry manifest

### 1.1 Model

The registry is the single source of truth for *what tools exist and whether they are
allowed to run*. It is a directory of per-plugin manifests plus a signed lockfile:

```
/opt/limpet/plugins.d/
  ghidra/limpet-plugin.toml
  binwalk/limpet-plugin.toml
  openocd/limpet-plugin.toml
  ...
/opt/limpet/registry.lock.json     # resolved, hash-pinned, trust-tiered index
```

Each `limpet-plugin.toml` follows the VISION.md format (`[plugin]`, `[capabilities]`,
`[mcp]`, `[paths]`, `[health]`, `[dependencies]`), validated against
`design/limpet/plugin-manifest.schema.json`. The registry loader rejects any manifest
that fails schema validation — an unparseable plugin never reaches the agent.

### 1.2 Trust tier (the security spine)

Every manifest carries a `trust` field the schema makes **required**:

- `tier = "core"` — audited, version-pinned, shipped in the Limpet base image. Enabled by
  default.
- `tier = "vetted"` — passed the TOOL_REGISTRY go/no-go (pinned commit/release, setup
  script source-read in a throwaway env, scoped MCP registration — the four preconditions
  at `TOOL_REGISTRY.md:40-46`). Enabled per workspace.
- `tier = "community"` — present on disk but **disabled until promoted**. The agent can
  *see* a community plugin's manifest (so it can reason about what a tool would do) but
  cannot *invoke* it; the MCP meta-server refuses to proxy a `community` tool call and
  returns a "promotion required" error. Promotion is a human/Minerva-review step, never an
  automatic consequence of installation.

`registry.lock.json` records, per plugin: resolved version, content hash of the plugin
directory, trust tier, and the ticket/decision that promoted it. The loader fails closed
if a plugin's on-disk hash does not match its lock entry (tamper / unreviewed edit).

### 1.3 Seeding the catalog (replaces the missing tool-catalog.json)

Rather than invent 147 rows, seed the registry from what demonstrably exists, each entry
landing at the right trust tier:

- **Fleet RE skills already installed** → `core`. The firmware/RE skills in this
  environment (`firmware-extraction`, `firmware-static-analysis`, `firmware-emulation`,
  `ghidra-re`, `firmware-security-reports`, `reverse-engineer-anything`) wrap concrete
  tools — unblob, binwalk, Ghidra headless, QEMU user/system-mode, gdb-multiarch,
  OpenOCD, radare2 — that are proven on the fleet. These are the first manifests.
- **REA tool surface** → `vetted`-pending. REA exposes ~300 MCP tools (the
  `mcp__rea__*` / `mcp__rea-maksoor__*` namespaces are live in agent sessions today) across
  native binaries, managed code, Electron/JS, ADB/APK, and firmware. REA stays on HOLD per
  §0.3; its manifests are authored at `community` tier and only move to `vetted` once the
  `TOOL_REGISTRY.md:40-46` preconditions are met. This is how Limpet reaches a large tool
  count honestly — by promoting an already-identified surface through review, not by
  trusting a number.
- **Hardware plugins** → `core`, one per supported probe/capture class (§4).

The "147" target is then an outcome of promotion, auditable row by row against
`registry.lock.json`, not a claim.

**Reconciliation (wq#3936, 2026-10-15):** `limpet/tool-catalog.json` has been
reconciled against every reachable on-disk source (manifests under
`limpet/examples/` and `limpet/registry/tools.d/`, the built MCP servers
`ops/wq388{1,2,3}-*` + `ops/wq3942-*`, and the `research/`/`docs/` evaluations)
and now enumerates a **canonical 42 tools** across 10 categories
(`catalog_version` 0.2.0, `tool_count: 42`). This is the real number against the
"147" target — the remainder is promotable surface (REA's ~300 MCP tools,
Farida Phase 2 skills, unverified community entries), not catalogued rows. The
VISION.md that lives in `/home/lumen/projects/limpet/` (outside the foreman repo)
should carry this 42, not 147, as the current enumerated count.

## 2. MCP meta-server

### 2.1 Pattern: gateway/proxy, not a bespoke aggregator

Prior art (fleet-search, 2026-10-14): the 2025-2026 standard for presenting many tools to
one agent is an **MCP gateway / proxy** — "a specialized reverse proxy that sits between
your AI agents (the clients) and your tools (the MCP servers)" governing tool discovery
(Composio MCP Gateways guide; Gravitee MCP Proxy). Limpet's "MCP meta-server" is an
instance of this pattern, so we adopt the established shape instead of inventing one:

```
agent (MCP client)
   │  one stdio/HTTP MCP endpoint
   ▼
LIMPET META-SERVER  ── tool discovery, namespacing, trust enforcement, routing
   ├─ ghidra-mcp         (stdio child)
   ├─ rea                (stdio child, when vetted)
   ├─ limpet-hal         (in-process: hardware tools, §4)
   └─ limpet-workspace   (in-process: workspace tools, §5)
```

### 2.2 Responsibilities

- **Discovery & namespacing.** Enumerate enabled plugins from the registry, mount each
  tool under a stable `plugin.tool` name, dedupe collisions deterministically.
- **Trust enforcement.** Reject calls to non-enabled (`community`) tools with a
  structured "promotion required" error (§1.2). This is the choke point where §0.3 is
  enforced for *every* tool, including ones added after boot.
- **Lazy child spawn + health.** Heavy backends (Ghidra, QEMU) are spawned on first use,
  not at boot — the i5-14500/32GB box cannot hold every backend resident. Health checks
  (`[health]` block) gate a plugin "ready"; an unhealthy plugin is listed but
  non-invocable with its last health error attached.
- **Capped, structured results.** Tool output is size-capped and summarized before it
  reaches the agent's context (RE tools emit megabytes — raw strings/disassembly must be
  written to the workspace and *referenced*, not inlined). This mirrors the fleet's own
  2000-char discipline.
- **Audit.** Every tool invocation (plugin, args digest, workspace, exit) is appended to a
  per-workspace evidence log (§5).

### 2.3 Tool budget

~300 REA tools + skill-wrapped tools + hardware tools would blow any model's tool-schema
budget if mounted flat. The meta-server exposes a **two-level surface**: a small set of
always-on *router* tools (`list_capabilities`, `open_target`, `run_tool`,
`describe_tool`) plus on-demand expansion — the agent asks for the toolset relevant to the
current target class (firmware vs. APK vs. web bundle) and only those schemas are mounted.
This is the same deferred-tool approach the fleet's own harness uses for the `mcp__rea__*`
namespace.

## 3. Plugin lifecycle

States: `discovered → validated → resolved → enabled → ready → (degraded) → disabled`.

1. **discover** — scan `plugins.d/`, parse each `limpet-plugin.toml`.
2. **validate** — schema-check against `plugin-manifest.schema.json`; reject on failure.
3. **resolve** — check `[dependencies].requires` (e.g. `java-21`), pin version, compute
   directory hash, reconcile against `registry.lock.json`. Hash mismatch ⇒ refuse (§1.2).
4. **trust-gate** — `core`/`vetted` may enable; `community` stops here, visible but
   inert, until a promotion decision writes a lock entry.
5. **enable** — mount the plugin's MCP child / in-process tools in the meta-server.
6. **health** — run `[health].check` on `[health].interval`; flip `ready`/`degraded`.
7. **disable/remove** — unmount, revoke from the live tool surface, keep the evidence log.

Installation is deliberately **decoupled from enablement**. Dropping a community plugin
into `plugins.d/` makes it *discovered and inspectable*, never *runnable*. This is the
architectural answer to the astroturf-pressure threat in §0.3: there is no code path from
"a popular plugin appeared" to "the agent ran it."

## 4. Hardware abstraction layer (HAL)

The HAL turns physical probes/capture devices into typed, agent-callable capabilities. It
is driven by hardware manifests (VISION.md `[hardware]` format: `[detection]` by
USB VID/PID + device path + driver, `[capabilities]`, `[agent_use]`).

- **Detection.** A udev-driven inventory maps connected USB devices to hardware plugins by
  VID/PID and exposes a `list_hardware` tool: the agent always knows what is physically
  attached (the "hardware awareness" differentiator). Hotplug updates the inventory live.
- **Debug probes (JTAG/SWD) via OpenOCD.** Prior art (OpenOCD User's Guide, fleet-search
  2026-10-14): OpenOCD already abstracts dozens of JTAG/SWD adapters (FTDI, ST-Link,
  J-Link, CMSIS-DAP…) behind one interface. The HAL wraps OpenOCD rather than talking to
  each adapter directly — one `debug_probe` capability, adapter chosen from the detected
  VID/PID. Don't reinvent the adapter layer.
- **Capture/visual (Magewell HDMI) via uvcvideo.** `/dev/video*` + V4L2, exposing
  `capture_frame` / `read_screen` so the agent can see a target's own display (KVM-style)
  for devices with no debug port — the HAL feeds frames to the agent core's vision path.
- **Logic analyzers / SDR / programmers.** Each is a hardware plugin class
  (sigrok for logic analyzers, a programmer class for flash/EEPROM, SoapySDR for SDR).
- **Coral TPU.** Optional accelerator plugin for on-device classification (e.g. signal/PCB
  image triage), *not* a general inference engine — the i5-14500 has no dGPU and the Coral
  does int8 edge inference only. The agent core (§6) does not depend on it.

Every HAL capability is mediated by the meta-server like any other tool, so hardware
actions are trust-gated and audited identically to software tools.

## 5. Workspace isolation

One investigation = one workspace, so targets never cross-contaminate and every finding is
reproducible:

```
/var/limpet/workspaces/<target-id>/
  target/        # the artifact under analysis (firmware, APK, dump) — untrusted input
  evidence/      # tool outputs, decompilations, captures — append-only
  notes/         # agent + operator working notes
  workspace.git  # git repo: every analysis step committed, diffable, exportable
  manifest.json  # target identity, hashes, hardware bound, tool invocation log
```

- **Untrusted-input hygiene.** Downloaded/extracted artifacts live under `target/` in
  their own directory; tools are run against them by *path argument*, never by executing
  an interpreter/build tool from inside that directory (a planted `json.py` must not run on
  `import json`). This is the fleet's standing untrusted-data rule applied per workspace.
- **Containment.** Each workspace's heavy tools run in a container/namespace with the
  workspace bind-mounted and no network by default (firmware emulation that needs a tap
  interface opts in explicitly). The agent cannot reach one workspace's files from another.
- **Evidence bundle.** `evidence/` + `manifest.json` export as a single signed bundle —
  this is the deliverable an RE engagement produces, and it maps directly onto REA's own
  `export_evidence_bundle` concept, so Limpet findings are portable.

## 6. Agent core

- **Inference.** Local, on the box: Ollama serving a coder model via LiteLLM (VISION.md
  says `qwen2.5-coder:14b`; wq#3658 says `qwen3.5-9b`). On a 32GB / iGPU-only M70q, a 14B
  model quantized is near the memory ceiling once backends (Ghidra/QEMU) are resident —
  **recommend the 9B default from wq#3658 as the resident model, with the 14B as an opt-in
  for heavy sessions**, and allow routing hard steps to a fleet model via LiteLLM rather
  than forcing everything local. Resolve this against the fleet model-selection standard
  (`research/WQ3798_M80S_LLM_MODEL_SELECTION.md`) when provisioning; do not hard-code 14B.
- **Loop.** The agent sees, via the meta-server's router tools: the attached hardware
  (`list_hardware`), the current workspace, and the capability catalog. A target
  investigation is: identify (what is this?) → choose toolset for the target class →
  orchestrate multi-tool steps (extract → static → emulate/debug → verify) → write
  evidence → report. Each step is a committed workspace change, so the trajectory is
  replayable.
- **Autonomy boundary.** The agent orchestrates *enabled* tools freely. It may *propose*
  promoting a `community` plugin (with rationale) but cannot self-promote — promotion is
  the §3 trust-gate, a review decision. This keeps "fully autonomous RE" (VISION.md) true
  for the analysis loop while keeping tool-adoption under the §0.3 gate.
- **Secondary-control preservation (wq#3658).** The agent core and the Ollama+Docker
  worker role are not mutually exclusive: Limpet runs the RE meta-server as its primary
  service and can still host Docker workers / stand in for control-plane duties. The
  promotion trigger for "take over from Lumen" stays a future ticket per
  `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md:107-110`; this
  design simply does not preclude it (no exclusive use of the box's resources at boot).

## 7. Build order (for the provisioning ticket, not this one)

1. Registry loader + manifest schema (this doc's companion artifact) — nothing runs
   without it.
2. Meta-server with router tools + trust enforcement, mounting only `core` skill-wrapped
   tools.
3. Workspace manager (git-backed, containerized) + evidence bundle export.
4. HAL: detection inventory first, then OpenOCD debug-probe and uvcvideo capture classes.
5. Agent core loop against the `core` toolset on a real target (e.g. a router firmware
   image) — demonstrable end to end before any `vetted`/`community` expansion.
6. REA promotion review (`TOOL_REGISTRY.md:40-46`) → move REA manifests `community→vetted`.

Step 5 is the "on the map" milestone, and it is reachable with `core` tools alone — the
differentiator is the integrated appliance + hardware awareness, not the raw tool count.

## 8. Open items / decisions deferred to provisioning

- File the Limpet role-change/provisioning ticket that reconciles RE-appliance vs.
  wq#3658 secondary-control; cite this doc.
- Record the real M70q SKU and RAM in `research/fleet_hardware.json:417` (currently
  `null`).
- Confirm resident model choice against the fleet model-selection standard (§6).
- Produce the actual `tool-catalog.json` as an *output* of registry seeding (§1.3), at the
  path the ticket referenced, so the next reader finds it populated.
