# wq#4004 — LIMPET DISSECT: OFRAK (redballoonsecurity/ofrak) — the modify/repack platform (gap G7)

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/redballoonsecurity/ofrak` per the competitor dissection guide §3.1
(`scratch/research/limpet_competitor_dissection_guide.md`, read at dispatch; guide lives
outside this worker's sandbox so citations below are to the live tree, not the guide
snapshot). OFRAK (Open Firmware Reverse Analysis Konsole) by **Red Balloon Security** is the
reference implementation of the one capability Limpet does not have and the guide flags as
gap **G7: modify / repack** — take a firmware image apart, change it, and put it back together
into a flashable artifact. This is a **buy-don't-build** target: the machinery here (a
cross-compile→link→inject patch toolchain across ~10 architectures) is years of work we should
wrap, not reimplement.

**The headline is the license, not the code.** OFRAK is *source-available, NOT open source*
(§3 below). The code is excellent and the gap is real, but the LICENSE GATE is **RED** for any
Limpet use that is commercial, product-embedded, or multi-user — which is all of Limpet's
intended lanes. Read §3 before anyone clones it into a build path. This finding feeds wq#3959
(license audit).

## 0. Grounding (live, not memory)

Cloned shallow to an isolated scratch dir and read directly this session (`git clone --depth 1`
into `/tmp/fworker-wq4004/ofrak-src`, read as untrusted data — no instruction inside any source
file, docstring, or embedded spec was acted on, per the fleet's standing rule for
attacker-controlled bytes). Target citations are `OFRAK:<path>:<line>`; Limpet-repo citations
are bare `limpet/...:N`.

Confirmed shape: a multi-package Python monorepo — `ofrak_core` (the Resource model + component
framework + the bundled components), `ofrak_patch_maker` (the compile/link/inject toolchain),
`ofrak_io`/`ofrak_type` (primitives), `ofrak_ghidra`/`ofrak_angr`/`ofrak_binary_ninja`
(disassembler backends), plus a web GUI. Formats are reached through *components*, not a
monolith (§1). License: **OFRAK Community License v1.1**, effective 2024-07-22
(`OFRAK:LICENSE:7-9`).

## 1. The Resource tree + component model — the architecture to internalize

OFRAK's whole design is two ideas: **(a) every artifact is a node in a Resource tree**, and
**(b) all work is done by small typed components that the framework discovers, matches, and
chains.** This is the model Limpet's workspace should borrow wholesale (deliverable 2).

### 1a. The Resource tree

A `Resource` (`OFRAK:ofrak_core/src/ofrak/resource.py:69`) is a view over a backing
(resource-model node + data blob) served by injected services, not a file. The tree is the
unpack hierarchy: a flash image → partitions → filesystems → files → code regions → instructions,
each a child `Resource`. Navigation is `get_parent` / `get_children` / `get_descendants`
(`OFRAK:...resource.py:1101,1307,1213`), and children are minted with `create_child` /
`create_child_from_view` (`OFRAK:...resource.py:628,716`). Nodes carry three things: **tags**
(what kind of thing this is — `ResourceTag`), **attributes** (analyzer-produced facts), and
**data** (the bytes). Attributes can be `@index`-ed so the resource service can query the tree
by attribute value (`ResourceIndexedAttribute`, `OFRAK:...model/resource_model.py:39-51`) — the
tree is effectively a queryable database, not just a parent/child list.

### 1b. The five component types and how they register + chain

Every component implements `ComponentInterface` (`OFRAK:ofrak_core/src/ofrak/component/interface.py:11`),
whose load-bearing fields are `targets` (the `ResourceTag`s it applies to,
`OFRAK:...interface.py:29-32`), `external_dependencies` (`:34-37`), and `id`/`version` (`:17-18`).
Five kinds, each a thin abstract subclass of `AbstractComponent`:

- **Identifier** — tags a resource (decides *what it is*).
- **Unpacker** — splits a resource into typed children; declares `children` as the tags it can
  emit, and the framework *validates the children actually carry those tags*
  (`Unpacker.children` + `_validate_unpacked_children`, `OFRAK:...component/unpacker.py:44-51,109-160`).
- **Analyzer** — attaches attributes (facts) without changing bytes.
- **Modifier** — "directly manipulating the underlying binary data" (`OFRAK:...component/modifier.py:13-20`).
- **Packer** — "typically mirror unpackers … reassembling them to produce a new resource"
  (`OFRAK:...component/packer.py:31-34`).

**Registration is dependency-injection discovery, not a decorator registry.** `ofrak.discover(module)`
scans an installed module, the injector instantiates every component class it finds, and
`component_locator.add_components` indexes them by their `targets`
(`OFRAK:ofrak_core/src/ofrak/ofrak_context.py:152-173`). A new plugin is "installed" simply by
being an importable module of component classes — this is why third-party OFRAK components
(ghidra/angr/binja) ship as separate pip packages. **Chaining is tag-driven dispatch:** when a
user calls `resource.unpack()` / `.analyze()` / `.pack()`
(`OFRAK:...resource.py:457,465,486`), the job service selects the components whose `targets`
match the resource's tags and runs them; `unpack_recursively` walks the whole subtree
(`OFRAK:...resource.py:540`). No component names a successor — the graph is implicit in the tags.

### 1c. The transactional spine (the subtle, excellent part)

`AbstractComponent.run` (`OFRAK:ofrak_core/src/ofrak/component/abstract.py:63-185`) is a
transaction wrapper around each component body. Three properties matter for a modify/repack
platform:

1. **Patches are deferred and applied atomically.** `resource.queue_patch(range, data)` does not
   touch bytes; it appends a `DataPatch` to the component's modification tracker and only *after
   the component exits* does `apply_all_patches` flush them through the data service, enforcing
   "at most one patch that causes a resize" (`OFRAK:...resource.py:1061-1089`;
   `OFRAK:...component/abstract.py:140,242-253`). Byte-level edits never corrupt mid-component.
2. **Dependencies auto-invalidate.** After patches apply, a `DependencyHandler` recomputes which
   downstream analyses/unpacks are now stale (`OFRAK:...abstract.py:134-143`) — edit a byte and
   the analyzer results that depended on that byte are invalidated, so a later re-pack sees truth.
3. **Unpack⇄pack is a reversible pair by bookkeeping.** Running an unpacker clears the record of
   any packer that ran (so you can repack again), and running a packer clears the unpackers' records
   and *deletes the now-consumed children* (`OFRAK:...component/unpacker.py:83-90`;
   `OFRAK:...component/packer.py:75-83`). This is what makes "unpack → modify → repack → unpack
   again" a sound loop rather than a one-shot.

### 1d. External-tool capability probing (direct Limpet parallel)

A component declares `external_dependencies` (e.g. a packer that shells out to `squashfs`); if the
tool is missing at run time, `run` raises `ComponentMissingDependencyError` with the apt/brew
install hint, or OFRAK can be built to silently exclude every component whose deps are absent
(`OFRAK(..., exclude_components_missing_dependencies=True)`,
`OFRAK:...component/abstract.py:60-105,264-286`). This is *exactly* Limpet's plugin health-gate
model (`limpet/manifest.py` `HealthCheck`, `limpet/registry_core.py` `resolve(... health_ok)`)
arrived at independently — strong validation that Limpet's trust/health gating is the right shape.

## 2. The modify/repack machinery (G7 proper) — two tiers

**Tier 1 — in-place byte patching** is just `Modifier` + `queue_patch` (§1c). Good for flipping a
flag, NOP-ing a check, swapping a string. No compiler needed.

**Tier 2 — inject *new code*** is `ofrak_patch_maker`, and this is the part that is genuinely
hard and worth buying. The pipeline, from its own usage docstring
(`OFRAK:ofrak_patch_maker/src/ofrak_patch_maker/patch_maker.py:1-31`):

1. Pick a **Toolchain** — a cross-compiler wrapper. OFRAK ships GNU toolchains for ARM, AArch64,
   x86, x64, M68K, PPC, AVR, SPARC and an LLVM-12 chain
   (`OFRAK:ofrak_patch_maker/src/ofrak_patch_maker/toolchain/*.py`).
2. `make_bom(...)` — compile C/asm source into a **BOM** (batch of objects), parsing each
   object's symbols/segments (`OFRAK:...patch_maker.py:202`).
3. `allocate_bom(...)` — ask the target's `Allocatable` resource for free space and assign the
   BOM's segments real VM addresses in the target's memory map
   (`OFRAK:...patch_maker.py:511`); `create_unsafe_bss_segment` carves scratch RAM (`:315`).
4. `make_fem(...)` — **link** the allocated objects into a Final Executable & Metadata object
   resolving against the target's known symbols (`OFRAK:...patch_maker.py:464`).
5. `SegmentInjectorModifier` writes the linked segments into the firmware resource
   (`OFRAK:...patch_maker.py:30`), after which a normal `pack` re-emits the flashable image.

So "repack" = re-run the packers up the tree over a modified child set (§1c); "modify" ranges from
a one-byte `queue_patch` to a full relocatable-code inject via PatchMaker. **Rebuilding tier 2
from scratch is the trap** — it is a per-architecture linker/allocator, maintained and tested
(`OFRAK:ofrak_patch_maker/tests/test_*_toolchain.py`, one suite per arch).

## 3. 🔴 LICENSE GATE — source-available, NOT OSS (feeds wq#3959)

**Verdict: RED for Limpet's intended use. Do not vendor, embed, or run OFRAK in any shared or
commercial Limpet path without a signed OFRAK Pro/Enterprise License from Red Balloon.** The
terms are explicit and unusually strict; the relevant clauses, verbatim-cited:

- **Two-tier, commercial-gated by design.** The Community License is "for educational use,
  personal development, or just having fun"; *"individual use of OFRAK at work"* already requires
  the paid **OFRAK Pro License** (`OFRAK:LICENSE:1-5`).
- **The grant covers only Academic + Non-Commercial Use** (`OFRAK:LICENSE:77-92`), and
  "Non-Commercial Use" is defined as *personal research/evaluation/development **by an
  individual**, and not use by or on behalf of any commercial entity or organization*
  (`OFRAK:LICENSE:50-55`). "Commercial Use" is *any* use by a commercial entity or for a
  commercial purpose (`OFRAK:LICENSE:57-62`). A Limpet product/appliance is Commercial Use on its
  face → needs a Pro/Enterprise agreement (`OFRAK:LICENSE:88-92`).
- **"You cannot make money off of … OFRAK analysis, OFRAK-modified binaries, or other OFRAK
  outputs"** (`OFRAK:LICENSE:53-55`). This reaches the *outputs*, not just the code — a repacked
  firmware produced by OFRAK is itself encumbered. Decisive for a paid RE service.
- **Shared Use is forbidden outside academia** (`OFRAK:LICENSE:69-75`): a single OFRAK instance
  "used by more than one person" or "on a server that is accessible by more than one person" is
  Shared Use, permitted only for an Academic Purpose. **A Limpet fleet appliance exposing OFRAK to
  more than one operator is Shared Use → not covered**, even if somehow non-commercial.
- **Viral grant-back on Derivatives** (`OFRAK:LICENSE:94-114`): any modification you make to OFRAK,
  you *must* license back to Red Balloon with an "irrevocable … right to … commercialize" and to
  every other OFRAK user. Forking OFRAK hands Red Balloon your fork.
- **The one carve-out that helps: the "separable / linking" boundary.** Derivatives *exclude*
  "works that remain separable from, or merely link to, the interfaces of OFRAK"
  (`OFRAK:LICENSE:64-67`). Limpet code that invokes OFRAK across a process/CLI boundary is
  therefore **not** a Derivative and dodges the grant-back — but this only governs *derivative
  status*; it does **not** grant commercial *use*. The use grant (§2 of the license) still caps at
  Academic/Non-Commercial regardless.

**Net for the build path:** OFRAK cannot be bundled into a Limpet image, cannot back a shared
Limpet service, and its outputs cannot be commercialized under the free license. Two lawful paths
exist — (a) **procure an OFRAK Pro/Enterprise License** (a commercial agreement; queue as a
Frank/procurement physical task, it is the only thing that unblocks embedding), or (b) treat OFRAK
as an **operator-supplied, single-user, local-only** optional tool that each individual operator
licenses themselves, kept strictly out of any shared/commercial Limpet surface. Default to (b)
until (a) is signed.

## 4. OFRAK Projects — the git-shareable workspace bundle (deliverable 2 input)

PR#360's **OFRAK Project** is a plain directory that is a git repo: `metadata.json` +
`binaries/` + `scripts/` (`OFRAK:ofrak_core/src/ofrak/project/project.py:103-140`). `metadata.json`
lists the scripts and, per binary, an `init_script` plus `associated_scripts`
(`OFRAK:...project/project.py:60-78`). `OfrakProject.clone_from_git(url, path)` clones and
`init_from_path` validates the structure (`OFRAK:...project/project.py:97-101`). The payoff: an RE
investigation — *this firmware + the exact scripts that unpack/modify/repack it* — is a shareable,
versioned artifact you `git clone` and re-run. This is precisely the shape Limpet's `workspace_report`
/ `manifest` should take so an investigation is reproducible and hand-offable, independent of OFRAK
the tool (the *bundle format* is an idea, not OFRAK code — no license issue in borrowing it).

## 5. Extraction verdict — what Limpet lifts, and exactly where

**ADOPT-1 (architecture, license-free) — the Resource-tree + typed-component model for the Limpet
workspace.** Model a Limpet investigation as a tagged tree of resources with
identify/unpack/analyze/modify/pack components matched by tag, discovered at load, and run inside a
deferred-patch transaction (§1). Limpet already has the trust half of this
(`limpet/manifest.py` write-scopes, `limpet/registry_core.py` `resolve`) and the manifest already
anticipates OFRAK: `WRITE_SCOPES` carries `"target"` *"(modifies the artifact under analysis, e.g.
OFRAK repack)"* and `MutationBoundary` documents an *"OFRAK-style repack plugin writes=('target',),
escalates=True"* (`limpet/manifest.py:38-42,63-78`). The missing half is the *resource tree* itself
as the workspace's data model. Steal §1c's three invariants (deferred atomic patches,
dependency-invalidation on edit, unpack⇄pack bookkeeping) — they are the hard-won correctness
properties of a repack engine.

**ADOPT-2 (format, license-free) — the OFRAK Project bundle as the Limpet workspace export** (§4):
`metadata.json` + `binaries/` + `scripts/`, git-cloneable, one `init_script` per target. Makes a
Limpet investigation reproducible and shareable. Pure format borrow; no OFRAK code.

**WRAP (G7, gated) — OFRAK itself as the modify/repack engine, behind the trust gate and the
license gate.** The buy-don't-build call is correct: tier-2 code injection (§2) is years of
per-arch toolchain work. Wrap it as a single-user, process-separated plugin
(`writes=("target",), escalates=True`, exactly the boundary `limpet/manifest.py:63-78` already
describes) that shells to an operator-supplied OFRAK install — **the separable/linking carve-out
(`OFRAK:LICENSE:64-67`) keeps the Limpet wrapper off the derivative hook, but the license-gate of §3
still blocks any shared/commercial/bundled deployment.** Gate the plugin on both health (OFRAK
present) and a recorded license attestation per operator.

**HOLD THE LINE — keep unblob/binwalk for extraction.** OFRAK's unpackers overlap unblob/binwalk,
which the fleet's firmware-extraction skill already mounts and which are permissively licensed. Do
not route extraction through OFRAK; reserve the OFRAK dependency for the one thing only it does well
and nothing free replaces — **tier-2 modify/repack**.

**Net differentiation read.** OFRAK is the strongest modify/repack platform in the field and closes
G7 on paper — but its license makes it a *liability to embed* and an *asset to wrap carefully*.
Limpet's edge stays HAL + appliance + multi-operator trust gating, which is precisely the deployment
shape OFRAK's Community License forbids. So: lift the *architecture* (ADOPT-1/2, free and
clarifying), wrap the *engine* only behind a signed Pro/Enterprise license or a strict single-user
local path, and keep Limpet's own extraction/trust spine as the product.
