# wq#3998 — LIMPET DISSECT: REA (morluto/rea), the primary software-tool-plane peer

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/morluto/rea` per the competitor dissection guide §1.1
(`scratch/research/limpet_competitor_dissection_guide.md:45-104`). Companion to the
landscape read in `design/wq3929_competitive_analysis.md` §1(2) — that doc placed REA in the
field; this one goes to the target×evidence level the guide asks for and turns three of REA's
patterns into concrete Limpet artifacts.

## 0. Identity, license, grounding

- Repo `morluto/rea`; npm `rea-agents`; site rea.tools. **MIT** (adoptable — wrap/vendor/learn,
  record in the license audit wq#3959). TypeScript, Node 22.x+, turbo monorepo. Viral since
  2026-10-10 (~250k stars / ~62.6k forks — the brief's "62K stars" was the fork count).
- One MCP server + an identical CLI that gives any coding agent (Claude Code, Codex, Cursor,
  Gemini CLI, Grok) a reverse-engineering tool-plane. **macOS-first**, Linux/Windows supported.
- **Grounding (live, not memory):** the REA MCP server is installed on the fleet, so the tool
  catalog below is read from the live `mcp__rea__*` surface in this session, cross-checked
  against the guide's inventory (`...dissection_guide.md:52-64`). Verbatim product language was
  fetched 2026-10-15 via `fleet-search read https://github.com/morluto/rea`. Prior fleet
  grounding: `design/wq3929_competitive_analysis.md:56-83`, `scratch/research/limpet_live_search_2026-10-14.md:24-30`.

REA's own one-line framing of its trust model, verbatim from the repo front page:

> "Analysis runs locally, and results include the evidence and limitations behind each conclusion."

## 1. Target × evidence matrix (the feature bar), diffed vs Limpet

Columns: **Target class** · what REA ingests · the REA tools that serve it · **Evidence it
yields** · **Limpet coverage today** (✅ core/plugin have · ◑ partial · ✗ gap · — out of lane).
Limpet "coverage" = the committed `core`/`vetted` plugin seed
(`design/wq3806_limpet_architecture.md:96-99` + the Maksoor inventory `TOOL_REGISTRY.md:15-30`),
plus the fact that REA itself is promotable through the TOOL_REGISTRY gate
(`design/wq3806_limpet_architecture.md:100-110`) — so most ✗ rows are "Limpet does not natively
cover this, but can mount REA for it."

| Target class | Input | REA tools (live catalog) | Evidence yielded | Limpet |
|---|---|---|---|---|
| Native binaries (ELF/Mach-O/PE) | binary | `open_binary`,`procedure_pseudo_code`,`procedure_assembly`,`list_strings`,`list_names`,`get_call_graph`,`xrefs`,`batch_decompile` | pseudocode, asm, strings, symbols, call graph, xrefs — each with provider + coverage note | ✅ ELF (Ghidra/radare2/objdump); ◑ Mach-O/PE (Linux-appliance lane) |
| Offline ELF layout | ELF | `inspect_binary_layout`,`list_segments`,`inspect_macho` | segments/sections/load commands (pwntools) | ✅ (radare2/readelf) |
| Go binaries | Go binary | `inspect_go_binary` | build info, module graph | ✗ roadmap |
| Rust binaries | Rust binary | `inspect_rust_binary` | cargo-auditable crate graph | ✗ roadmap |
| WASM | `.wasm` | `inspect_wasm_artifact`,`compare_wasm_interfaces` | imports/exports/interfaces (WABT) | ✗ roadmap |
| EVM bytecode | bytecode | `inspect_evm_interface` | selectors, arg types, state mutability | ✗ roadmap |
| SQLite DB | `.sqlite` | `inspect_sqlite_database` | schema, tables, row samples | ✗ roadmap |
| Recorded Linux crash | core/recording | `inspect_recorded_crash` | fault addr, backtrace, register state (pwndbg/GDB) | ✗ roadmap |
| JavaScript / Electron | bundle/app | `analyze_javascript_application`,`analyze_web_bundle`,`recover_javascript_sources`,`inspect_electron_fuses`,`trace_web_module_imports`,`export_web_scripts` | modules, imports, source maps, routes, IPC, native add-ons, fuses | ✗ roadmap |
| Live website (Chrome) | URL | `capture_web_screenshot`,`inspect_web_page`,`observe_web_session`,`inspect_web_event_listeners` | DOM structure, scripts, network, screenshots | ✗ (out-of-lane-leaning) |
| Saved network capture | HAR/SAZ/mitm | `inspect_web_network_capture` | requests, hosts, headers | ◑ (overlaps pcap lane) |
| Recorded packets | pcap/pcapng | `inspect_packet_capture`,`inspect_pcap_capture`,`inspect_packet_fields` | endpoints, DNS, TLS SNI (TShark) | ◑ tshark gap G6 (`wq3929:144-146`) |
| .NET assemblies | DLL/EXE | `inspect_managed_artifact`,`inspect_managed_members`,`import_managed_reconstruction`,`project_managed_application_graph` | CIL, types, members, app graph | ✗ roadmap |
| Android APK | APK | `inspect_android_package`,`search_android_classes`,`decode_android_resources`,`inspect_android_method` | classes, methods, resources (JADX+JDK) | ✅ jadx/apktool (Maksoor) |
| Android device | adb target | `list_adb_packages`,`pull_adb_package`,`capture_adb_screen`,`read_adb_logcat`,`inspect_adb_device` | packages, logs, screen, file xfer, dumpsys | ◑ adb not in core seed |
| JEB projects | `.jdb2` | `open_jeb_project`,`decompile_jeb_item` | decompiled items via REA_JEB_MCP_URL | — (commercial engine) |
| Flutter apps | Dart snapshot | `identify_flutter_build`,`inspect_dart_aot` | engine/snapshot provenance, AOT classes | ✗ roadmap |
| Firmware | image | `extract_firmware`,`inspect_firmware_regions` | unpacked tree, region map (Binwalk/Unblob) | ✅ unblob/binwalk core |
| Logic signals | sigrok capture | `decode_signal_recording`,`inspect_signal_decoders` | decoded protocol frames (sigrok-cli) | ✅ sigrok (Maksoor) |
| Apple bundles / plists | bundle | `inspect_plist`,`inspect_asset_catalog`,`decode_interface_builder`,`inspect_keyed_archive` | plist values, assets, NIB/XIB, keyed archives | — (macOS lane) |
| Swift types | Mach-O | `analyze_swift_types`,`demangle_swift` | Swift type metadata, demangled names | — (macOS lane) |
| Process behaviour (PTY) | live process | `capture_process_scenario`,`observe_native_calls`,`compare_process_captures` | output, interactions, exit, fs observations, run diffs | ✗ roadmap |
| Dynamic native | running target | `instrument_with_frida`,`start_gdb_session`,`start_rizin_debug_session` | hooked calls, breakpoints, traced values | ◑ frida (Maksoor), gdb core — no REA-style scenario capture |
| Static↔runtime correlation | both | `correlate_static_and_runtime` | static sites tied to observed runtime behaviour | ✗ roadmap |
| Reconstruction coverage | an investigation | `build_reconstruction_obligation_ledger`,`evaluate_reconstruction_coverage`,`verify_reconstruction` | open obligations, coverage %, verified claims | ✅ built — `limpet/obligations.py` (A1→G2, wq#3965) |
| Evidence portability | an investigation | `export_evidence_bundle`,`import_evidence_bundle` | portable evidence bundle | ◑ workspace is git-backed; maps to bundle (`wq3806:227-229`) |

**Diff summary.** REA's breadth is its moat and the feature bar. Limpet natively matches it only
on the firmware/ELF/APK/logic-signal lane (the fleet's existing strength) plus the obligation
ledger. Everything else REA offers is reachable for Limpet by **promoting REA through the
TOOL_REGISTRY gate as the software-tool-plane and wrapping the HAL + appliance around it** — the
designed path, not a gap (`design/wq3806_limpet_architecture.md:100-110`). This confirms the §4
thesis of wq#3929: software-tool breadth is commoditized, so Limpet must lead with
HAL + appliance + trust-gate, not tool count.

## 2. Missing target classes for the Limpet roadmap

The ✗ rows above are the roadmap. Most are **cheap wrappers over a single CLI** Limpet can
promote as a `vetted` plugin — the value REA proves is that each becomes a first-class target
once it carries the standard evidence+limitations contract. Ordered by cost/value:

1. **Go** — `go version -m` + symbol/pclntab read. Trivial.
2. **Rust** — `cargo-auditable` crate-graph extraction. Trivial.
3. **WASM** — `wabt`/`wasm-tools` import/export/interface dump. Cheap.
4. **.NET / CIL** — `ilspycmd` or `monodis`. Cheap, high value (managed malware).
5. **SQLite** — `sqlite3` schema + sampled rows into the workspace. Trivial.
6. **EVM bytecode** — selector/ABI/mutability recovery (`evmole`-class). Cheap, niche.
7. **Flutter / Dart AOT** — `blutter`/`reflutter`. Medium (snapshot format churns).
8. **Recorded-crash triage** — pwndbg/GDB batch over a core file. Cheap; pairs with the
   dynamic lane Limpet already seeds (gdb-multiarch, `wq3806:98`).
9. **Process-capture (PTY scenario)** — record output/syscalls/fs-effects of running a target.
   Medium; the honest Linux analogue of REA's macOS process capture, and it feeds the
   static↔runtime correlation idea.
10. **JS/Electron** — source-map recovery + `asar`/fuse inspection. Medium; large surface.

Out of lane (do **not** chase native): macOS-only classes (Apple bundles, Swift types, JEB) and
live-website Chrome capture — mount REA for these rather than reimplement.

## 3. The no-sudo mutation boundary + anti-impersonation, as plugin-contract fields

REA ships two security conventions as *documentation*. Limpet should carry them as *schema* —
declarative, validated, fail-closed manifest fields — so the trust gate reasons about them
instead of trusting prose. Both are implemented in this branch in `limpet/manifest.py`:

- **No-sudo mutation boundary → `[mutation]` table.** REA's boundary is "analysis runs locally,"
  it operates only on processes it owns (the repo's "Linux owned-process prerequisite": a
  `procps`-compatible `ps` must be on PATH for process-ownership checks), and it installs an
  engine like Hopper only "with approval." That is a *declared blast radius*, never an inferred
  one. Limpet encodes it as a closed `WRITE_SCOPES` taxonomy (`limpet/manifest.py:42`) and a
  `MutationBoundary` dataclass (`limpet/manifest.py:63`): a plugin declares `escalates`,
  `network`, and `writes` (subset of `none`/`workspace`/`target`/`device`/`host`). REA's own
  boundary is the all-default instance (`escalates=False`, `network=False`, `writes=("none",)`);
  an OFRAK-style repack plugin declares `writes=("target",)`, a flash programmer
  `writes=("device",)`, and `escalates=True` is the explicit, auditable opt-out of the no-sudo
  default rather than a silent `sudo` in a start hook. Validation fails closed on an unknown
  scope (`limpet/manifest.py` `_validate_mutation`), and **undeclared ≠ read-only** — a manifest
  with no `[mutation]` table yields `mutation is None`, which the trust gate treats as *unknown*,
  not *safe*.
- **Anti-impersonation → `[source].canonical`.** REA's front page warns, verbatim: *"A similar
  repository name or copied README does not establish that a download is official."* Limpet
  encodes the defence as a `canonical_source` field (`limpet/manifest.py`, read from
  `[source].canonical`): the authoritative upstream for a plugin name. A plugin index/registry
  then compares a drop-in's declared source against the canonical URL registered for that name,
  so a name-squatting copy carrying an identical README is detectable. This is the *declaration*
  half of plugin trust; the *verification* half (signing, marketplace vetting) stays owned by
  wq#3945 (`design/wq3944_parity_breakdown.md` A9). The real-world stakes are on record: 42k+
  exposed OpenClaw instances plus malicious ClawHub skills (`scratch/research/limpet_live_search_2026-10-14.md:85-89`).

Scope note: these are pure, unit-tested schema fields (manifest.py is filesystem/network-free by
design, `limpet/manifest.py:10`). They do not touch the registry daemon or the signing path —
consistent with how wq#3944 split adopt-items into their own build tickets.

## 4. Mutation-testing adoption note

REA's headline verification lane is **Stryker mutation testing** (`stryker.config.mjs`), on top
of vitest + jscpd copy-paste detection + semantics tests
(`...dissection_guide.md:72-74,91`). Mutation testing is the right fit for Limpet's tool layer
specifically: a line-coverage number says a validator *ran*, but a mutation score says it would
*catch a regression* — exactly the property a fail-closed security boundary needs. The
`manifest.py` validators added here (mutation-boundary, canonical-source) are the natural first
target: flip a `<=` to `<`, drop a `not`, weaken a scope check, and a surviving mutant is a hole
in the trust gate. **Adopt:** add a mutation-testing pass (mutmut or cosmic-ray for Python) over
`limpet/manifest.py` + `limpet/registry_core.py` as a CI gate, scoped to the plugin-contract and
resolver code where a silent weakening is a security bug, not merely a coverage miss. File as a
QUALITY build ticket under the wq#3944 adopt ledger. Design-only here.

## 5. Extraction verdict

| REA pattern | Verdict | Limpet hook |
|---|---|---|
| Per-target evidence+limitations contract | adopt | G1 / A2 (wq#3964) — already filed |
| Reconstruction obligation ledger | adopted | `limpet/obligations.py` (A1→G2, wq#3965) |
| No-sudo mutation boundary | **adopted here** | `limpet/manifest.py` `[mutation]` table |
| Anti-impersonation (canonical source) | **adopted here** | `limpet/manifest.py` `[source].canonical`; verify half → wq#3945 |
| Stryker-style mutation testing | adopt | new QUALITY ticket, scoped to manifest.py/registry_core.py |
| Missing target classes (Go/Rust/WASM/EVM/.NET/SQLite/Flutter/crash/process/JS) | wrap-as-plugin | §2 roadmap, mount REA in the interim via TOOL_REGISTRY |
| macOS-only classes (Apple/Swift/JEB), Chrome capture | ignore (out of lane) | mount REA, do not reimplement |
| Breadth-as-moat | avoid | lead with HAL + appliance + trust-gate (wq#3929 §4) |
