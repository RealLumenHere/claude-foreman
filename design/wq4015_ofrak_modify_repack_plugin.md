# wq#4015 — LIMPET DESIGN: OFRAK integration as modify/repack plugin (gap G7)

Status: design. Author: build worker, 2026-10-15. This is the canonical design for wrapping
OFRAK (Red Balloon Security) as a Limpet **modify/repack** plugin — the one capability Limpet
lacks and the competitor guide flags as **gap G7**: take a firmware image apart, change it, and
put it back together into a flashable artifact.

It builds directly on three prior deliverables and does not re-derive them:

- **wq#4004** (`design/wq4004_ofrak_redballoon_dissection.md`) — the OFRAK dissection. Read its
  §1 (Resource tree + component model), §2 (the two-tier modify/repack machinery), §3 (the
  **RED license gate**), §4 (OFRAK Projects as a shareable workspace bundle), §5 (extraction
  verdict: WRAP G7 behind the trust + license gate). This doc realizes §5's `WRAP` verdict.
- **wq#3810** (`design/wq3810_limpet_plugin_system.md`, code in `limpet/`) — the plugin
  lifecycle and the `limpet-plugin.toml` schema (`limpet/manifest.py`). The OFRAK manifest in
  §6 is valid against that schema.
- **wq#3806** (`design/wq3806_limpet_architecture.md`) — §4 the **energize-confirm gate** and §5
  **workspace isolation**. §4 and §5 here map OFRAK onto both.

**Reference implementation already in flight.** wq#3967 (PR #1936, verified by wq#3987 —
`reports/wq3987_verify_wq3967_ofrak_modify_repack_plugin.md`) built the CLI core of this plugin:
`ops/wq3967-limpet-ofrak-a1/{ofrak_ops.py,ofrak_cli.py,selftest.py}`, a `limpet/examples/ofrak.toml`
manifest, and a `limpet/tool-catalog.json` row, all at **community tier** with the meta-server
`cli_tools` entries shipped `enabled: false` pending a license purchase. That PR is the engine
wrapper; it is unmerged as of this writing. This design is the spec that PR realizes and extends
it to the five ticket items it does not yet cover: **(2)** the three MCP tools, **(3)** the
workspace-manager mapping, **(4)** the repack confirm gate, the **Docker** packaging of **(1)**,
and the **Ghidra routing** note. Where this doc and PR #1936 disagree on a name, this doc is the
intended target and the delta is called out in §8.

Citations: OFRAK upstream as `OFRAK:<path>:<line>` (via wq#4004, read live that session);
Limpet-repo as bare `limpet/...:N`; OFRAK docs grounded live this session against
`ofrak.com/docs/user-guide/disassembler-backends/ghidra.html` and
`ofrak.com/docs/environment-setup.html`.

## 1. Deployment decision (sets everything else): single-user, local, process-separated

wq#4004 §3 is decisive and non-negotiable: OFRAK's **Community License v1.1** grants only
Academic + Non-Commercial, **individual** use, forbids **Shared Use** (one instance reachable by
more than one person) outside academia, and reaches the **outputs** ("you cannot make money off
OFRAK-modified binaries"). A Limpet fleet appliance exposing OFRAK to more than one operator is
Shared Use on its face (`OFRAK:LICENSE:69-75`), and Limpet's meta-server is exactly that shape —
one shared instance driven by whichever agent session calls it.

Two lawful paths (wq#4004 §3 "Net for the build path"):

- **(a) Procure an OFRAK Pro/Enterprise License.** A commercial agreement with Red Balloon. This
  is the only thing that unblocks a bundled or shared deployment. It is a purchase only Frank can
  make → **queued as a physical/procurement task, never a gate that blocks this design.** The
  meta-server entries ship `enabled: false` and carry a licensing-hold note until it is signed
  (as PR #1936 already does).
- **(b) Default until (a) is signed: operator-supplied, single-user, local-only, process-separated.**
  Each operator installs their own OFRAK and licenses it themselves; Limpet never bundles it,
  never backs a shared service with it, and invokes it **across a process/container boundary**.
  The separable/linking carve-out (`OFRAK:LICENSE:64-67`) keeps the Limpet wrapper off OFRAK's
  viral derivative-grant hook precisely *because* the wrapper only invokes OFRAK's interfaces
  across that boundary — it never imports OFRAK into Limpet's own address space.

Every design choice below follows from (b): **Docker is the process boundary** (§2); the plugin
is **community-tier** and gated on a recorded per-operator license attestation (§6, §8); the
meta-server tools are **disabled by default** (§3).

## 2. Packaging — OFRAK as an operator-supplied Docker container

OFRAK ships three install paths (`ofrak.com/docs/environment-setup.html`): PyPI `pip install
ofrak` (simplest), from source (Git-LFS), and **a Docker image built from a build-config YAML**
("the most consistent and comprehensive environment"). wq#3967 took the pip path and hit the
sandbox wall — OFRAK's pinned aiohttp Cython extension does not build against Python 3.14
(`reports/wq3987...:29-31`). **Docker is therefore the design choice, not pip**: it pins OFRAK's
own tested Python (3.8) and its non-pip external tools (squashfs, Ghidra, etc.) inside an image
Limpet neither builds into its own artifact nor shares, satisfying §1(b)'s process boundary.

Design:

- **The image is operator-supplied.** The plugin does not build or vendor an OFRAK image into any
  Limpet artifact (license §1). The operator builds or pulls their own from Red Balloon's
  build-config (`redballoonsecurity/ofrak` registry), including the `disassemblers/ofrak_ghidra`
  package in the build config's `packages_paths` if they want the Ghidra backend (§5). The plugin
  references the image by a name the operator sets; health-check (§6) fails closed if it is absent.
- **One container invocation per OFRAK call, per workspace.** Each `limpet_ofrak_*` tool runs
  `docker run --rm` (or `exec` into a per-workspace long-lived container) with **only that
  workspace bind-mounted** and **`--network none` by default** — the workspace-isolation
  containment rule (wq#3806 §5: "heavy tools run in a container/namespace with the workspace
  bind-mounted and no network by default"). OFRAK's own GUI/analysis server listens on port 8877
  (`ofrak gui -H 0.0.0.0 -p 8877`); Limpet does **not** expose it — the plugin drives OFRAK
  headless via scripts passed by path, never the web GUI.
- **Untrusted-input hygiene (wq#3806 §5).** The firmware under analysis lives in the workspace
  `target/` dir and is passed to OFRAK **by path argument**; OFRAK scripts run against it, never
  by executing an interpreter from inside `target/`. The container is the namespace that enforces
  "the agent cannot reach one workspace's files from another."

## 3. The three MCP tools

Three meta-server tools, named per the ticket, each a thin router over one OFRAK operation run in
the §2 container. OFRAK's native call shapes are fixed from wq#3987's upstream re-verification
(`reports/wq3987...:78-93`): `OFRAK()` → `.run(main)` →
`create_root_resource_from_file(path)` → `unpack_recursively()` / `pack_recursively()` /
`flush_data_to_disk(path)`, with byte edits via `BinaryPatchModifier` + `BinaryPatchConfig(offset,
data)`. Each tool's declared write-scope is the contract the trust gate reads before it runs
(`limpet/manifest.py:38-42` WRITE_SCOPES; `:62-76` MutationBoundary):

| MCP tool | OFRAK op | write-scope | confirm |
|---|---|---|---|
| `limpet_ofrak_unpack` | `create_root_resource_from_file` + `unpack_recursively` → emit the Resource tree | `workspace` (writes unpack output into the workspace only; the target bytes are untouched) | none — autonomous |
| `limpet_ofrak_modify` | `BinaryPatchModifier`/`Modifier` on a named resource (in-place byte patch, §tier-1 of wq#4004 §2); tier-2 `PatchMaker` code-inject deferred to a follow-on | `workspace` (the modification is staged against the unpacked tree in the workspace, not yet flushed to a flashable image) | none — autonomous (edits are reversible and workspace-local until repack) |
| `limpet_ofrak_repack` | `pack_recursively` + `flush_data_to_disk` → **a new flashable image** | `target` (produces the modified artifact) | **CONFIRM required — §4** |

- **`unpack` and `modify` stay autonomous.** They only write into the workspace; OFRAK's
  transactional spine (wq#4004 §1c) makes staged patches deferred and reversible, and the
  unpack⇄pack bookkeeping means a repack can always be redone. Nothing destructive has happened
  until `repack` flushes an image. This keeps "fully autonomous RE" (VISION.md) true for the
  analysis loop.
- **`repack` is the one gated call** (§4). It is where staged edits become a flashable artifact
  — one `dd`/flash step away from a bricked device.
- **Tool I/O is resource-addressed, not file-addressed.** `unpack` returns the tree as node ids +
  tags + captions (OFRAK `get_id`/`get_caption`/`get_tags`, re-verified at
  `reports/wq3987...:86-89`); `modify`/`repack` take a `resource_id` naming the node to act on, so
  the agent operates on the tree the way OFRAK's model intends (wq#4004 §1a), not on raw offsets it
  has to track itself. `resource_id=None` means root (note: PR #1936's non-root `resource_id` path
  is implemented but untested — `reports/wq3987...:108-117`; a test is owed before it ships enabled).

## 4. The repack gate — reconciling "energize-confirm" with a file-producing step

The ticket calls for an "energize-confirm gate on repack (destructive — creates flashable
image)." Precision matters here, because wq#3806 §4's **energize-confirm gate is defined for
hardware** — "applies probe power, drives JTAG/SWD, **writes flash**, flips a GPIO/reset line…"
(`design/wq3806_limpet_architecture.md:214-228`). A repack **does not energize hardware**; it
writes a file. So the gate splits cleanly into two distinct confirms, and conflating them would
either leave repack ungated or mis-classify it:

1. **`limpet_ofrak_repack` is a `target`-scope destructive confirm.** It is gated by the **same
   confirm primitive** the energize gate uses (the operator confirm attached to one specific call,
   wq#3806 §4 / target-wizard-spec §4.3), **generalized to a `target`-scope mutation**: the
   meta-server must refuse to invoke `repack` without an explicit operator confirm attached to
   that call. Rationale — a repacked image is the artifact an operator will flash; producing it
   silently and autonomously is the step that makes an autonomous brick possible. The manifest
   declares `writes=("target",)` so the trust gate sees this blast radius before the tool is ever
   enabled (`limpet/manifest.py:69-71` names exactly "an OFRAK-style repack plugin
   writes=('target',)").
2. **Flashing the produced image to a device is the §4 energizing action proper** — a separate,
   `device`-scope capability that **writes flash**, and it fires wq#3806 §4's energize-confirm
   unconditionally. The OFRAK plugin **stops at the image file**; it never drives a programmer.
   Handing that image to a flash-programmer plugin (`writes=("device",)`, wq#3806 §4) is a
   distinct, separately-confirmed step. This boundary is deliberate: it keeps the one plugin that
   *can* produce a flashable image from also being the plugin that *can* flash it, so neither
   confirm can be bypassed by the other.

Both confirms are **policy, not per-session judgment** (wq#3806 §4, Frank 2026-10-15): the agent
may plan and queue a repack (and a subsequent flash), but each executes only after its own
operator confirm, regardless of trust tier or how many prior steps succeeded. A call with no
classification defaults to gated (fail closed).

## 5. Ghidra routing — one Ghidra, not two

OFRAK carries its own disassembler backends (Ghidra/Binary Ninja/angr/Capstone — wq#4004 §0), and
Limpet already ships a Ghidra plugin (`limpet/examples/ghidra.toml`) and a Ghidra MCP
(`limpet/examples/ghidra-mcp.toml`, which depends on `ghidra >=11.0`). Running a **second** Ghidra
inside the OFRAK container would double the heaviest backend on a 32GB iGPU-only box (wq#3806 §6
notes Ghidra+QEMU residency is already near the memory ceiling). The ticket's instruction — "route
OFRAK to Limpet's Ghidra instance" — is grounded by OFRAK's own config mechanism:

OFRAK's Ghidra backend is configured by `ofrak_ghidra.yml`, imported via `python -m
ofrak_ghidra.config import ofrak_ghidra.yml`, which supplies OFRAK with **the Ghidra install
paths and the Ghidra *Server address and credentials*** it connects to
(`ofrak.com/docs/user-guide/disassembler-backends/ghidra.html`). OFRAK requires a running Ghidra
server before it can use Ghidra analysis (`python -m ofrak_ghidra.server start`). Design:

- **Point `ofrak_ghidra.yml`'s Ghidra-server address at Limpet's existing Ghidra instance** rather
  than starting a second server inside the OFRAK container. Limpet's `ghidra` plugin owns the one
  Ghidra install and server; OFRAK becomes a *client* of it. This is the whole of the routing:
  OFRAK's Ghidra-backed analyzers (used by some unpackers/analyzers in the Resource tree) resolve
  against the same Ghidra project Limpet's own Ghidra MCP uses.
- **Manifest encodes the routing as an optional dependency.** The OFRAK plugin declares
  `[optional_dependencies] ghidra = ">=11.0"` (§6): OFRAK's tier-1 byte-patch and most
  unpack/repack work needs no disassembler, so Ghidra must never *block* OFRAK (hence optional,
  not required — `limpet/manifest.py:90-95` soft-dep semantics), but when Limpet's `ghidra` is
  active the registry reports it, and the operator's `ofrak_ghidra.yml` is wired to it so OFRAK's
  Ghidra-dependent components light up without a redundant install.
- **No duplication with Limpet's Ghidra MCP.** Limpet's own Ghidra MCP (`ghidra-mcp.toml`) stays
  the agent's disassembly/decompile surface; OFRAK is **not** exposed as a second decompiler. OFRAK
  uses Ghidra internally only to satisfy its own component graph, and only ever against the single
  shared Ghidra. If the operator does not want Ghidra in OFRAK at all, they simply omit
  `disassemblers/ofrak_ghidra` from the Docker build config (§2) and OFRAK runs with Capstone.

## 6. The `limpet-plugin.toml` manifest

Canonical manifest, valid against `limpet/manifest.py` (kind/category/mutation/source/health all
shape-checked by `validate_manifest`, `limpet/manifest.py:211-268`). The filename stem must equal
`[plugin].name` (wq#3810 lifecycle step 1), so this installs as
`/etc/limpet/plugins.d/ofrak-modify-repack.toml`:

```toml
[plugin]
name = "ofrak-modify-repack"
version = "1.1.0"                 # tracks OFRAK Community License v1.1 era; bump with the wrapper
kind = "tool"
category = "firmware"             # one of limpet/manifest.py CATEGORIES
summary = "OFRAK modify/repack engine (unpack -> patch -> flashable image), wrapped as a single-user Docker-isolated plugin"
maintainer = "lumen-limpet"
license = "OFRAK-Community-1.1"   # source-available, NOT OSI; see [mutation] + §1
conflicts = []

[source]
canonical = "https://github.com/redballoonsecurity/ofrak"   # anti-impersonation (manifest.py canonical_source)
git = "https://github.com/redballoonsecurity/ofrak"
rev = "master"

[optional_dependencies]
# Route OFRAK's Ghidra backend to Limpet's one Ghidra (ofrak_ghidra.yml server addr, §5).
# OPTIONAL, never required: tier-1 byte-patch and most unpack/repack need no disassembler,
# so a missing/disabled ghidra must never block OFRAK (manifest.py soft-dep semantics).
ghidra = ">=11.0"

[mutation]
# Declared no-sudo blast radius the trust gate reads BEFORE enabling (manifest.py:62-76).
# writes=("target",) is exactly the "OFRAK-style repack plugin" the schema names (manifest.py:69-71):
# repack produces the modified artifact. NOT ("device",): this plugin stops at the image file,
# it never drives a programmer (§4). escalates=true: OFRAK repack of some formats needs root in
# its own container (mounts, squashfs); it is the explicit, auditable opt-out of no-sudo.
escalates = true
network = false                   # --network none by default (§2); OFRAK runs offline
writes = ["target"]

[health]
# Fail closed if the operator-supplied OFRAK container is absent or the license attestation
# is unrecorded. selftest does an unpack->patch->repack round-trip in the container and checks
# the repacked image differs only in the patched region (the real check lives in PR #1936's
# integration test, reports/wq3987...:118-128).
command = "ofrak-modify-repack --selftest"
interval_seconds = 300
timeout_seconds = 120
```

Notes:
- **Community tier, disabled by default.** Per §1 and wq#3967, the meta-server registers the three
  tools `enabled: false` with a licensing-hold note; an operator flips them on only after (a) a
  Pro/Enterprise license is signed, or (b) they attest a personal single-user license. The
  `[health]` command gates on that attestation being recorded, so a plugin with no attestation
  stays `unhealthy` → never started (wq#3810 lifecycle step 3).
- **Why optional, not required, on ghidra:** making `ghidra` a hard `[dependencies]` entry would
  leave OFRAK `blocked` whenever Ghidra is disabled (wq#3810 step 5 transitive block), even though
  OFRAK's core modify/repack does not need it. Soft-dep is correct (§5).

## 7. Workspace-manager integration — OFRAK Project ↔ Limpet workspace

wq#4004 §4 / ADOPT-2: an OFRAK **Project** is a plain git-cloneable directory — `metadata.json` +
`binaries/` + `scripts/`, one `init_script` per binary plus `associated_scripts`
(`OFRAK:ofrak_core/src/ofrak/project/project.py:103-140,60-78`). A Limpet **workspace** is also a
git repo — `target/ evidence/ notes/ workspace.git manifest.json` (wq#3806 §5,
`design/wq3806_limpet_architecture.md:238-245`). The two are the same idea; map them so an OFRAK
investigation *is* a Limpet workspace and neither tool owns a second copy of the bytes:

| OFRAK Project | Limpet workspace | mapping |
|---|---|---|
| `binaries/<fw>` | `target/<fw>` | the firmware under analysis; one OFRAK binary ↔ the workspace target. No copy — the container bind-mounts `target/` (§2). |
| `scripts/*.py` (`init_script`, `associated_scripts`) | `evidence/ofrak/scripts/` committed to `workspace.git` | the exact unpack/modify/repack scripts, versioned with the investigation → replayable (wq#3806 §5 "every analysis step committed"). |
| `metadata.json` | a `[ofrak]` block in the workspace `manifest.json` | binary→script associations recorded in the workspace's own manifest so `limpet-workspace` sees them without parsing OFRAK's format. |
| repacked image (`flush_data_to_disk`) | `evidence/ofrak/repacked/<fw>.bin` + a `manifest.json` invocation-log entry | the §3 `repack` output is an evidence artifact with its hash logged; the §4 confirm is recorded against it. |

- **Direction of ownership: Limpet workspace is the source of truth; the OFRAK Project is a
  *view* materialized into it,** not a parallel store. `limpet_ofrak_unpack` on a workspace target
  lays down `scripts/` + resource-tree state under `evidence/ofrak/`; it never creates an OFRAK
  Project outside the workspace. This keeps the untrusted-input and cross-workspace-isolation
  guarantees (wq#3806 §5) intact — the container only ever sees one workspace.
- **Obligation + critic gates still apply (wq#3806 §6).** A repack that answers an open RE
  question resolves a ledger obligation with the `evidence/ofrak/repacked/...` ref; a finding that
  cites a repacked image goes through the critic pass like any other. OFRAK is just another
  evidence-producing tool inside the existing workspace discipline — it does not get its own
  parallel audit path.
- **Export unchanged.** The workspace evidence bundle (wq#3806 §5) already carries `evidence/` +
  `manifest.json`; the OFRAK scripts and repacked image ride in it, so an OFRAK investigation is
  as portable/hand-offable as any other Limpet engagement — and reproducible *without* OFRAK the
  tool present, because the scripts and hashes are in the bundle (the *format* borrow of wq#4004
  §4, which carries no license encumbrance — only OFRAK's code does).

## 8. Build status, deltas, and what is still owed

**Built (PR #1936 / wq#3967, verified wq#3987), unmerged:** the engine wrapper —
`ops/wq3967-limpet-ofrak-a1/{ofrak_ops.py,ofrak_cli.py,selftest.py}` doing unpack / list-resources
/ patch-bytes / patch-strings / repack over real OFRAK (API shape re-verified against upstream,
`reports/wq3987...:71-93`); a `limpet/examples/ofrak.toml` manifest; a `limpet/tool-catalog.json`
`ofrak` row at `trust: community`; five meta-server `cli_tools` entries shipped `enabled: false`.

**Deltas this design sets (PR #1936 → target):**
- **MCP tool names.** This design names the three meta-server tools `limpet_ofrak_unpack` /
  `limpet_ofrak_modify` / `limpet_ofrak_repack` (§3, per ticket). PR #1936's CLI verbs
  (`unpack`/`patch-bytes`/`patch-strings`/`repack`) are the engine under them; `modify` subsumes
  `patch-bytes`/`patch-strings`.
- **Packaging.** This design mandates **Docker** isolation (§2); PR #1936 used pip and hit the
  Py3.14 build wall. The Docker path is the fix *and* the §1(b) process boundary.
- **Manifest name.** §6 uses `ofrak-modify-repack`; PR #1936's example is `ofrak.toml`. Pick one
  at merge — `ofrak-modify-repack` states the capability and leaves the bare `ofrak` name free for
  a future unpack-only/extraction manifest (though extraction stays unblob/binwalk per wq#4004 §5
  HOLD-THE-LINE).

**Still owed (follow-on tickets, out of this design's scope):**
- The three MCP tool wrappers in the meta-server (`ops/wq3808-limpet-mcp-meta-server-a1/`),
  carrying the §4 repack confirm and the §3 write-scopes.
- The §4 repack **confirm** wiring into the meta-server invocation path (generalize the
  energize-confirm primitive to a `target`-scope mutation confirm).
- The §7 workspace-manager mapping in `limpet-workspace` (`[ofrak]` block in `manifest.json`;
  `evidence/ofrak/` layout).
- The `ofrak_ghidra.yml` → Limpet-Ghidra-server wiring helper (§5), plus the per-operator license
  attestation the `[health]` command checks (§6).
- A test for PR #1936's non-root `resource_id` patch path (`reports/wq3987...:108-117`) before any
  tool ships `enabled: true`.

## 9. One-paragraph summary

Wrap OFRAK — not reimplement it (wq#4004 §5: tier-2 code-inject is years of per-arch toolchain
work) — as a **community-tier, single-user, Docker-isolated** Limpet plugin, because OFRAK's
Community License forbids the shared/commercial deployment that is Limpet's whole shape (§1). Three
MCP tools (`limpet_ofrak_unpack`/`modify`/`repack`, §3) route to OFRAK in a per-workspace
network-less container (§2); only `repack` is gated, by a `target`-scope operator confirm distinct
from the hardware energize-confirm that governs the later flash-to-device step (§4). OFRAK's Ghidra
backend is pointed at Limpet's one Ghidra server via `ofrak_ghidra.yml`, declared as an *optional*
dependency so it never blocks core repack (§5). An OFRAK Project maps onto a Limpet workspace with
the workspace as source of truth (§7). The manifest (§6) declares `writes=("target",)` so the trust
gate sees the blast radius before the plugin is ever enabled, and ships disabled until a license is
attested. The engine wrapper already exists (PR #1936); this is the spec it realizes and the
follow-on work it still owes (§8).
