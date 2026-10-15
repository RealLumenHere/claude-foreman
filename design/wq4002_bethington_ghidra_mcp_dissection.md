# wq#4002 — LIMPET DISSECT: bethington/ghidra-mcp (schema-budget + lazy-load + cross-binary hash-match)

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/bethington/ghidra-mcp` per the competitor dissection guide §2.2
(`scratch/research/limpet_competitor_dissection_guide.md`). Fourth in the dissection series:
wq3998 set the *tool-breadth* bar, wq3999 the *methodology* bar, wq4000 the *code-critic* bar;
this one dissects the **tool-count leader** among GhidraMCP forks — the project whose
"244–256 tools" number drove the schema-budget argument in
`design/wq3929_competitive_analysis.md:181-199` — and finds that the leader itself has
repudiated the count as a metric.

## 0. Grounding (live, not memory) — and a version correction

Cloned `--depth 1` into `/tmp/fworker-wq4002/ghidra-mcp-src` and read directly this session,
as untrusted data (no instruction inside the target tree was acted on). Citations are
`GMCP:<path>:<line>`. Prior-art web search was attempted and is down fleet-wide this session
(`fleet-search web`: all SearXNG engines suspended, no BRAVE_API_KEY — known ISSUE-LOG row
#1332 / wq#2595); the primary source for a dissection is the cloned tree itself, which was
read in full where load-bearing.

**Version correction.** The ticket (and wq3929/wq3995) record **v5.14.2, 244–256 tools**.
Live tree: **v7.0.0** (`GMCP:pom.xml:9`, `GMCP:pyproject.toml:3`), Ghidra 12.1.4, Java 21,
and the authoritative catalog `tests/endpoints.json` holds exactly **209 tools**. The
"244–256" range was never a real number: the project's own changelog documents that six
published surfaces disagreed with the catalog and each other (272, 267, 251, 243, …) until
PR #525 derived every published count from the catalog and pinned them with
`tests/unit/test_published_counts.py` (`GMCP:CHANGELOG.md:838-850` — which also caught a
literal SOH control byte shipped inside the "Provides 256 MCP tools" string since v5.17.0).
So our recorded range was an artifact of the competitor's own count drift.

**Provenance / license (for the wq3959 audit):** Apache-2.0 (`GMCP:LICENSE:1-3`), originally
derived from LaurieWired/GhidraMCP with NOTICE retained (`GMCP:NOTICE:1-6`), copyright Ben
Ethington 2025-2026. Adoptable as a `vetted` plugin; no license obstacle.

**Architecture (two halves):** a Java Ghidra plugin/extension (`com.xebyte`, GUI and a
separate headless server build — `GMCP:src/main/java/com/xebyte/headless/GhidraMCPHeadlessServer.java`)
exposing HTTP endpoints plus a self-describing `GET /mcp/schema`; and a Python **bridge**
(`GMCP:python/bridge_mcp_ghidra/`, FastMCP) that fetches that schema and registers MCP tools
dynamically (`GMCP:python/bridge_mcp_ghidra/registry.py:551-562,408-453`). Of the 209
catalog tools, 205 are served by the GUI plugin, 190 by headless, 186 by both
(`GMCP:CHANGELOG.md:145`). Tools carry `@McpTool(path, description, category, access)`
annotations in Java; category = tool group.

## 1. Deliverable 1 — tool-count is commoditized: CONFIRMED, and strengthened

The 209-tool catalog spans 18 categories (counted live from `tests/endpoints.json`):
program 33, datatype 32, function 23, analysis 20, server 18, debugger 16, listing 12,
documentation 11, project 11, xref 8, malware 5, utility 5, comment 4, symbol 4, headless 3,
emulation 2, getter 1, system 1.

Three live facts upgrade wq3929's "§4 caveat holds" to *settled*:

1. **The leader is shrinking its own count on purpose.** v7.0.0 is a consolidation release:
   272 → 251 → 209, merging "dozens of single-purpose readers into `get_functions`,
   `find_functions` and `list_program_items`" (`GMCP:CHANGELOG.md:143-158`,
   `GMCP:docs/Context-Window-Analysis.md:15-19`). A project that marketed "3x more tools
   than any competing implementation" (`GMCP:README.md:82`) now treats raw count as debt.
2. **Their own measurement says count was never the constraint.** The full 251-tool catalog
   measured 12,922 tokens ≈ 6.5% of a 200K window; their stated conclusion: "Token count
   alone is not the limiting factor. **Discoverability** … is the real challenge"
   (`GMCP:docs/Context-Window-Analysis.md:27-31`).
3. **Counts drift unless derived.** Six contradictory published counts, fixed only by
   deriving from one catalog and pinning with a test (`GMCP:CHANGELOG.md:838-846`). Any
   competitive matrix row keyed to a fork's advertised tool count (ours included) is
   unstable data; wq3929's matrix should treat counts as order-of-magnitude only.

Action for `design/wq3929_competitive_analysis.md`: correct the bethington row to
**v7.0.0 / 209 tools (deliberately consolidated from 272)** and drop the 244–256 range.

## 2. Deliverable 2 — lazy tool loading, validated against Limpet's two-level meta-server

### 2a. Their mechanism (implemented, defaulted, battle-tested)

- **Lazy is the default** (`_lazy_mode = True`, `GMCP:python/bridge_mcp_ghidra/state.py:438`).
  On connect, only `CORE_GROUPS = {"listing", "function", "program"}`
  (`GMCP:python/bridge_mcp_ghidra/config.py:167`) — 68 of 209 endpoints — plus 8 always-on
  **management tools**: `list_instances`, `connect_instance`, `list_tool_groups`,
  `load_tool_group`, `unload_tool_group`, `check_tools`, `search_tools`, `import_file`
  (`GMCP:python/bridge_mcp_ghidra/config.py:109-118`).
- **Group = category.** `load_tool_group(group)` registers that category's tools from the
  cached schema and fires `tools/list_changed`; `unload_tool_group` removes them, with
  default groups protected from unload
  (`GMCP:python/bridge_mcp_ghidra/registry.py:456-505`).
- **Discovery is self-healing.** `search_tools`/`check_tools` search the *full* cached
  schema, including unloaded tools, and return the exact `load_tool_group("...")` call
  needed to make a hit callable
  (`GMCP:python/bridge_mcp_ghidra/static_tools.py:424-539`).
- **The forcing event was a hard provider limit, not token cost.** Eager was the default
  until 2026-08-25; advertising all ~253 tools made Gemini reject the whole request before
  any call — `400 INVALID_ARGUMENT: "The specified schema produces a constraint that has
  too many states for serving"` — because it compiles function declarations into a
  constrained-decoding state machine (`GMCP:python/bridge_mcp_ghidra/state.py:415-428`).
  "Not merely expensive — over a hard limit." This is the citable external proof for
  Limpet's two-level design.
- **Compatibility edges they already paid for:** clients that ignore `tools/list_changed`
  need `--no-lazy`; where argv isn't controllable (container ENTRYPOINT, `uvx`, registry
  configs) the escape hatch is `GHIDRA_MCP_LAZY=0` (`GMCP:python/bridge_mcp_ghidra/state.py:430-437`,
  `cli.py:549-565`); stateless HTTP is incompatible with lazy (per-session registry state)
  and warns accordingly (`GMCP:python/bridge_mcp_ghidra/cli.py:596-600`).
- **Trust metadata rides the schema.** Each tool def carries `read_only`/`destructive`;
  the bridge maps these to MCP annotations, and an *unclassified* tool deliberately gets
  none — because guessing read-only from the HTTP method is wrong for mutating GETs, and
  an auto-approved mutating call "while an agent is still planning is the failure this is
  meant to avoid" (`GMCP:python/bridge_mcp_ghidra/registry.py:380-394`). Writes take a
  server-side write lock (`GMCP:src/test/java/com/xebyte/offline/WritesTakeTheWriteLockTest.java`).

### 2b. Validation verdict for Limpet

Limpet's two-level surface (`design/wq3806_limpet_architecture.md:163-171`) is the same
answer to the same problem — **independently converged, so the design is validated** — but
with one uncomfortable live fact: **theirs is implemented and defaulted; ours is
design-only.** The current meta-server prototype advertises every registered tool flat in
`tools/list` (`ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:246-251`). A Limpet
workspace that mounts a 209-tool Ghidra plugin through today's meta-server reproduces
exactly the Gemini-breaking eager surface. Differences that matter when we implement:

| axis | bethington | Limpet (design) |
|---|---|---|
| expansion unit | category group (`datatype`, `server`, …) | target-class toolset (firmware vs APK vs web) |
| discovery of unloaded tools | `search_tools` returns the loading call | router tools `describe_tool`/`run_tool` |
| client without `tools/list_changed` | degraded — needs `--no-lazy` | unaffected (router tools never change the list) |
| trust | advisory read_only/destructive annotations | enforced trust tiers at the choke point |

**ADOPT (mechanics, into the meta-server when built):** (1) self-healing discovery — a
search over the *full* catalog whose misses return the exact expansion call; (2) default
groups protected from unload; (3) an env-var escape hatch alongside the CLI flag, for
ENTRYPOINT/uvx contexts; (4) cite the Gemini constrained-decoding 400 in wq3806 §2.3 as the
external justification. **KEEP (ours):** router-tools beat `list_changed`-dependent dynamic
registration for client compatibility — their own docs document the clients it breaks; and
trust *enforcement* stays our edge over their advisory annotations (wq3929's read holds:
lazy-load is table stakes; the trust-tier gate around it is the differentiator).

## 3. Deliverable 3 — cross-binary hash-match + documentation transfer (workspace feature; overlaps wq3906)

### 3a. What they built

The `documentation` category (11 tools) is a pipeline: **hash → match → transfer → archive**.

- **Hash:** `get_function_hash` computes a normalized opcode hash — mnemonics kept;
  intra-function references → `REL+<offset>`; external refs → `CALL_EXT`/`DATA_EXT`/`EXT_REF`;
  other addresses → `ADDR`; immediates < 0x10000 kept, larger → `IMM_LARGE`; SHA-256 over
  the normalized stream (`GMCP:src/main/java/com/xebyte/core/DocumentationHashService.java:108-180`).
  Rebase- and layout-independent: the same function in two versions hashes equal if only
  addresses moved. One call can hash *every* function, paged, filtered to
  documented/undocumented (`GMCP:...DocumentationHashService.java:59`).
- **Match (fuzzy tier, for functions whose bodies changed):** `find_similar_functions_fuzzy`
  and paged `bulk_fuzzy_match` (threshold 0.7) score a weighted blend of numeric features
  (instruction/block/call counts), set features, and structural features incl. per-basic-block
  normalized hashes (`GMCP:src/main/java/com/xebyte/core/BinaryComparisonService.java:395-411`).
  They also ship Ghidra **BSim** workflow scripts (`GMCP:ghidra_scripts/Analyze_BSim*.java`)
  — for heavy matching they ride Ghidra's own similarity database rather than their blend.
- **Transfer:** `apply_documentation` (one or many functions) and
  `merge_program_documentation` — copy ALL RE documentation source→target at matching
  addresses in a **single server-side transaction**: 17 counted categories (names,
  signatures, plate comments, locals, calling convention, no-return, tags, data types, data
  definitions, all 4 comment types, labels, globals, external locations, bookmarks,
  equates), **idempotent** ("re-running on an already-merged target only fills gaps"), with
  `dry_run=true` counting without writing
  (`GMCP:src/main/java/com/xebyte/core/DocumentationHashService.java:772-802`).
- **Archive:** `archive_ingest_function`/`_program` push docs to an external cross-version
  archive keyed `binary_name + version + address`, version extracted from the Ghidra
  project path; **off by default**, opt-in via `GHIDRA_MCP_ARCHIVE_URL`
  (`GMCP:...DocumentationHashService.java:1250-1290`). "Document once, apply everywhere"
  (`GMCP:README.md:85`) is thus: hash-match a new version against documented ancestors,
  then transact the docs across.

### 3b. Fit against wq3906 (firmware image diff) — complementary, not duplicate

`design/wq3906_limpet_firmware_diff.md` already covers the **matching** half at both
granularities: stage-2 file-level hash gating (`:72-99`) and stage-4 per-function
similarity via BinDiff/Diaphora parsing, explicitly "never re-implements the matching"
(`:112-123`). What wq3906 does **not** have is the **write-back half**: its output is a
diff *report*; nothing carries the old version's function names/types/comments forward onto
the new version's workspace. That transfer step is exactly what turns a version diff from a
one-shot report into compounding workspace knowledge.

**ADOPT (workspace feature, extends wq3906 — the ticket's deliverable 3):**

1. Treat documentation as a **propagatable workspace asset**: after a wq3906 stage-4 run,
   every `matched` function above threshold gets the prior version's docs applied to the
   new workspace — names, types, comments — with provenance recorded in the evidence log
   (`transferred_from: <workspace>@<version>, similarity, differ`).
2. Steal the **transaction semantics**, not the matcher: idempotent gap-filling merge +
   `dry_run` count + per-category applied/planned counters (their 17-key counter map is the
   right report shape). Matching stays BinDiff/Diaphora/BSim per wq3906's hold-the-line.
3. The **normalized-opcode exact-hash tier** is worth adding *below* BinDiff: it is cheap
   (no differ run), deterministic, and resolves the unchanged-function majority before any
   similarity engine starts — same shape as wq3906's stage-2 "hash gate makes a
   thousands-of-file image tractable" argument (`wq3906:83`), one level down.
4. The cross-version **archive** maps onto the Limpet workspace store rather than an
   external HTTP service — per-target lineage is already a workspace concept; copy their
   identity scheme (name+version+address) and their off-by-default posture.

## 4. Deliverable 4 — coverage gaps mined from the 209-tool catalog

Categories where bethington is deep and Limpet's current plugin plan (wq3806/wq3810/wq3929)
has nothing equivalent — each a candidate plugin capability or roadmap row, none a reason
to re-implement in core:

1. **Datatype engineering at scale (32 tools, largest category).** Struct create/resize
   workflows, Hungarian-notation and naming-convention *enforcing* write tools — "v5.0
   moves naming conventions, type safety, and documentation standards into the tool layer"
   (`GMCP:README.md:88,94`). The insight worth copying is conventions-as-tool-contract, not
   style guides in prompts: Limpet workspace write tools should validate names/types at the
   tool boundary.
2. **Ghidra Server multi-user (18 tools).** Repositories, checkout/checkin, version
   history, admin (permissions, terminate-checkouts), credential resolution via env/.env
   (`GMCP:README.md:753-765,1191-1209`). This is a *multi-seat collaborative RE* model no
   other dissected competitor has. Limpet's workspace is single-operator; a shared-backend
   seat model is a genuine white-space row for wq3929 — but it rides Ghidra Server, so for
   Limpet it is plugin surface, not core.
3. **Live debugger integration (16 TraceRmi tools + 13 opt-in WinDbg proxies,
   `GMCP:python/bridge_mcp_ghidra/config.py:121-160`).** Static-to-dynamic address
   translation, breakpoints, tracing, watch. Limpet has no dynamic-analysis story beyond
   QEMU emulation; the fleet's REA has Frida/GDB. Gap acknowledged; plugin-tier.
4. **P-code emulation (2 tools) + PCode-graph dataflow (analysis category).** Architecture-
   independent micro-execution without a target environment — complements, not duplicates,
   the firmware-emulation skill's QEMU.
5. **Malware heuristics (5 tools):** crypto-constant detection, anti-analysis technique
   finding, IOC extraction with context, API call-chain analysis, behavior detection.
   Cheap, high-leverage read-only tools; natural Limpet workspace plugins.
6. **Multi-instance operation:** `list_instances`/`connect_instance` against several Ghidra
   GUIs/ports, with Docker compose for GUI, headless, bridge, and an nginx-fronted
   multi-instance file (`GMCP:docker/docker-compose.multi.yml`). Limpet's meta-server
   assumes one child per plugin; parallel per-binary instances is a scaling pattern to note
   in wq3806.
7. **Operational hygiene worth copying regardless of domain:** one published tool count
   derived from the catalog and pinned by test (§1); script execution off by default
   because it is arbitrary code in the Ghidra process (`GMCP:README.md:721`); an AGENTS.md
   that tells agent authors tool names are *not* a stable API ("tools are merged, renamed
   and refactored there freely, so build nothing on its …", `GMCP:README.md:75`) — an
   honesty Limpet's plugin-facing docs should replicate.

**Setup scripts (ticket item):** Windows-first one-shot PowerShell
(`GMCP:ghidra-mcp-setup.ps1:6-11`, 1331 lines: -SetupDeps/-BuildOnly/-Deploy/-Clean), a
cross-platform Python setup CLI (`GMCP:tools/setup/`: Ghidra install, Maven, Python env,
.env, spawn), release wheel via `uv tool install` (`GMCP:README.md:50`), Gradle now the
documented default build (`GMCP:CHANGELOG.md:851-853`). Nothing here changes Limpet's
appliance posture — their setup burden (JDK 21 + Ghidra + Maven/Gradle + Python bridge) is
precisely the installation friction Limpet's preprovisioned appliance deletes.

## 5. Net differentiation read

bethington/ghidra-mcp is the strongest *depth-on-one-backend* competitor dissected so far:
209 consolidated tools, a defaulted lazy-load with real-world provider-limit scars, a
complete hash→match→transfer→archive documentation pipeline, and the only multi-user story
in the field. None of it moves Limpet's moat — HAL + appliance + trust-gate — and two of
its hard-won lessons (count-is-not-a-moat, lazy-load-is-table-stakes) *are our own wq3929
conclusions confirmed by the leader itself*. The deltas that go on the roadmap: implement
the two-level meta-server surface (it is currently paper), add doc-transfer to the wq3906
workspace loop, and mine its datatype/malware/debugger categories as plugin-tier
capability rows rather than core work.
