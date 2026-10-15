# wq#3932 — RE plugin ecosystems: patterns for the Limpet plugin system

Status: research/design. Author: build worker, 2026-10-15. Blocks: Limpet plugin
system refinement and community strategy.

This document surveys ten reverse-engineering / security plugin ecosystems and
extracts the patterns that matter for Limpet's plugin system. The question it
answers: **given how every established RE tool already lets people extend it,
which of Limpet's choices are right, which are gaps, and what should the
community contribution workflow look like?** Limpet's own design is cited to the
committed specs, not restated from memory: `design/wq3810_limpet_plugin_system.md`
and the reference implementation under `limpet/`.

## 0. Method and grounding (verified vs. described)

Doctrine is prior-art-first. At dispatch the fleet general web search and the
GitHub API were both down — SearXNG general engines are CAPTCHA/suspended and no
`BRAVE_API_KEY` is configured (wq#2595), and the GitHub token is unauthenticated
(401). Same outage the sibling ticket logged (ISSUE-LOG #1325). The working
backend was `fleet-search read <url>` (fetch + extract), used to anchor the
highest-value ecosystems against their canonical docs. Grounding tiers:

- **Verified live (docs fetched 2026-10-15):** r2pm
  (`book.rada.re/tools/r2pm/intro.html`), Binary Ninja plugins
  (`docs.binary.ninja/guide/plugins.html`), Ghidra extensions
  (`ghidra-sre.org/InstallationGuide.html`).
- **Described from established knowledge:** IDA (FLIRT/Lumina), Frida, Volatility,
  YARA, Nmap NSE, Metasploit, OpenClaw ClawHub. These are stable, well-documented
  systems; feature claims below reflect their published design as of the Jan-2026
  knowledge cutoff and are marked *(described)*. Re-verify against live docs when
  search is restored before any claim is used to justify a build commitment.

## 1. Ecosystem-by-ecosystem

### (1) OpenClaw plugins/skills — ClawHub marketplace *(described)*
A general agent platform with a hosted marketplace (ClawHub) of plugins/skills.
Discovery is marketplace-browse + search; install is one click / one CLI command
pulling a packaged bundle; updates are marketplace-pushed with version pinning.
Manifest is a declarative descriptor (name, version, entrypoint, permissions,
category) — JSON/YAML shaped. This is the *curated central marketplace* end of the
spectrum: strong discovery and update story, trust mediated by the marketplace
operator rather than by signatures. The relevant lesson for Limpet is the
discovery UX (search + categories + install-by-name), not the hosting.

### (2) Ghidra extensions — zip + properties, GitHub-distributed *(verified live)*
Ghidra (NSA) is extended two ways: **scripts** (Java/Python dropped into a
`ghidra_scripts` dir, discovered by the Script Manager) and **extensions** (a
module adding analyzers, plugins, loaders, or filesystems). An extension ships as
a `.zip` whose root carries an `extension.properties` descriptor (name, description,
author, createdOn, version) plus a `Module.manifest`; it is installed from the GUI
("File → Install Extensions") or by unzipping into
`$GHIDRA_INSTALL_DIR/Extensions/Ghidra/` or the per-user
`~/.ghidra/.ghidra_<ver>/Extensions/`, then restarting. Extensions are built with
Gradle (`gradle buildExtension` against `GHIDRA_INSTALL_DIR`). **There is no
central marketplace** — discovery is GitHub search and word of mouth. Key
third-party extensions in the LLM-RE wave (described): GhidrAssist and Gepetto
(LLM-assisted decompilation comments), ghidra-mcp / GhidraMCP (MCP server exposing
the decompiler to agents), Cartographer (coverage visualisation), BSIM (NSA's own
function-similarity search, ships in-tree). Pattern: rich plugin *capability*,
zero packaging/discovery/trust infrastructure.

### (3) radare2 r2pm — git-cloned package DB of shell scripts *(verified live)*
`r2pm` is radare2's package manager. `r2pm init` git-clones the package database
repo (`radareorg/radare2-pm`); `r2pm -U` updates it by `git pull`. A package is a
single file under `db/<name>` — a **shell script** defining metadata and
lifecycle functions (description, dependencies, install/uninstall that usually
build from source; some packages support binary builds and even wasm). Installs
go to `~/.local/share/radare2` by default or system-wide with `-g`. A larger,
less-curated `radare2-extras` repo holds third-party packages. Pattern:
git-repo-as-registry, imperative (script) package format, build-from-source,
dependency hints but no real solver, no signing.

### (4) Binary Ninja plugins — plugin.json + a generated central index *(verified live)*
Plugins live in the user plugin folder (`~/.binaryninja/plugins/` on Linux, etc.),
mostly Python (some C++, preliminary Rust). Each plugin carries a **`plugin.json`**
manifest (name, author, version, API/version/platform compatibility, dependencies,
license). The **Extension Manager** (GUI, Ctrl-Shift-M, or the `PluginManager`
API) installs from a curated community index — the `vector35/community-plugins`
repo aggregates every plugin's `plugin.json` into one `plugins.json` the manager
reads. Compatibility is advertised per platform/Python version; a mismatch shows
"Force Install" with a warning. Headless use is first-class: `pip install
binaryninja` plus a headless license. Pattern: **declarative JSON manifest +
curated central index + GUI/API install + explicit compatibility metadata** — the
most complete non-marketplace model in this survey, and the closest prior art to
where Limpet should land.

### (5) IDA plugins — FLIRT, Lumina, scattered community repos *(described)*
IDA has no package manager. Three distinct extension mechanisms: **plugins**
(native `.dll`/`.so` or IDAPython files dropped into `plugins/`, discovered at
startup); **FLIRT** (Fast Library Identification and Recognition Technology —
`.sig` pattern files, compiled from `.pat`, that let IDA name statically-linked
library functions); and **Lumina** (a Hex-Rays-hosted cloud server storing
function metadata/names keyed by function hash, pushed/pulled interactively — a
crowd-sourced knowledge layer, not a code-plugin registry). Community discovery is
curated GitHub lists (e.g. the long-lived `onethawt/idaplugins-list`). IDA 9 added
`idalib` for headless embedding. Pattern: powerful but fragmented; Lumina is the
one interesting idea here — a shared *data* layer distinct from the *code* layer.

### (6) Frida — scripts, Codeshare, and the gadget *(described)*
Frida's unit of extension is a JavaScript agent script, loaded via `frida -l
script.js`, the Python/Node bindings, or `frida-compile` for multi-module agents.
**frida-gadget** is a shared library embedded into a target app (configured by a
sidecar gadget-config JSON) for cases where you can't spawn under the Frida
toolset — note this isolates/instruments the *target*, it is not a plugin-sandbox
for Frida itself. Distribution is informal: GitHub, gists, and **Codeshare**
(`frida.re/codeshare`) — a hosted registry of community scripts you run directly
with `frida --codeshare <user>/<slug>`. `frida-tools` ships the CLIs. Pattern:
no manifest, no versioning, a hosted script registry (Codeshare) as the one
discovery mechanism; trust is entirely on the operator running arbitrary JS.

### (7) Volatility plugins — Python classes, auto-discovered *(described)*
Volatility 3 plugins are Python classes subclassing
`volatility3.framework.interfaces.plugins.PluginInterface`, auto-discovered by
import from the framework plugin dirs plus any `--plugin-dirs`. A plugin declares
its config/inputs via `get_requirements()` (typed requirement objects) rather than
a separate manifest — **metadata lives in the code**. Symbol tables (ISF JSON) are
a separate distributable layer, downloaded/packed independently of plugins.
Community plugins live in `volatilityfoundation/community`. Pattern: in-code
metadata, import-based discovery, requirements-as-code, no signing/sandbox.

### (8) YARA rules — repos, no package manager *(described)*
YARA rules are `.yar` text files (rule name, `meta:`, `strings:`, `condition:`).
There is no package manager. Distribution is GitHub repos — `Yara-Rules/rules`,
`Neo23x0/signature-base` (Florian Roth), vendor feeds — updated by `git pull`.
Aggregation/quality tooling has emerged: **yara-forge** compiles many sources into
tiered rulesets (core/extended/full) with quality filtering, and `yaraQA` lints.
YARA-X is the newer Rust engine. Pattern: pure data artifacts, repo distribution,
aggregation tools doing the curation/quality job a registry otherwise would.

### (9) Nmap NSE — Lua scripts + a generated script.db *(described)*
NSE scripts are Lua files in Nmap's `scripts/` dir. Each carries structured
in-script fields (`description`, `author`, `license`, `categories`, and a
`dependencies` table naming other scripts that must run first). `nmap
--script-updatedb` regenerates **`script.db`**, the index mapping scripts to
categories that `--script <category>` resolves against. Categories (safe,
intrusive, vuln, exploit, discovery, …) are the discovery/selection primitive.
Scripts ship with Nmap; community scripts arrive via the Nmap repo. Pattern:
in-script metadata, a locally-generated index, a real (if simple) inter-script
dependency declaration, and categories as the user-facing taxonomy.

### (10) Metasploit modules — Ruby files, monorepo PR workflow *(described)*
Modules are Ruby files under `modules/<type>/` (exploits, auxiliary, post,
payloads, encoders, nops, evasion). Metadata is **in the module** — the `info`
hash: Name, Description, Author, License, References (CVE/URL), Platform, Targets,
DisclosureDate, and a reliability/rank. Loaded by path; users drop local modules
in `~/.msf4/modules`. Community contribution is a **GitHub PR to
`rapid7/metasploit-framework`** gated by acceptance standards and the `msftidy`
linter (style + required metadata checks). Pattern: in-code metadata, path-based
discovery, a disciplined monorepo-PR + linter contribution workflow — the best
*contribution-process* prior art in this survey.

## 2. Pattern analysis across ecosystems

### Manifest format
| Ecosystem | Manifest | Form |
|---|---|---|
| **Limpet** | `limpet-plugin.toml` | **TOML, declarative, separate file** (`limpet/manifest.py`) |
| OpenClaw ClawHub | descriptor *(described)* | JSON/YAML, declarative |
| Binary Ninja | `plugin.json` | JSON, declarative, separate file |
| Ghidra extension | `extension.properties` + `Module.manifest` | Java-properties, separate |
| r2pm | `db/<name>` | shell script, imperative |
| Volatility | `get_requirements()` | Python, in-code |
| NSE | in-script fields | Lua, in-code + generated `script.db` |
| Metasploit | `info` hash | Ruby, in-code |
| IDA / Frida / YARA | — | none (binary sig / JS / rule text) |

Takeaway: the RE field splits into **declarative-separate-manifest** (Limpet,
Binary Ninja, Ghidra extensions, ClawHub) and **metadata-in-code** (Volatility,
NSE, Metasploit). In-code metadata is convenient for single-file script ecosystems
but can't be validated or indexed without executing/parsing the plugin's own
language. Limpet's choice of a declarative TOML file parsed with stdlib `tomllib`
and no code execution (`limpet/manifest.py` is pure) is the right call for a
system that wants to validate and index *before* trusting a plugin — it matches
Binary Ninja's JSON model, the most mature non-marketplace prior art.

### Discovery mechanism
Three models: **curated central index** (ClawHub marketplace; Binary Ninja's
`vector35/community-plugins` → `plugins.json`; r2pm's `radare2-pm` db repo; Frida
Codeshare), **locally-generated index** (NSE `script.db`), and **GitHub-search /
curated-list** (Ghidra, IDA, Volatility, YARA, Metasploit modules not in-tree).
The central-index model wins on UX (search, install-by-name, update) every time it
exists. Limpet today is GitHub-repo-per-plugin with no index
(`design/wq3810_limpet_plugin_system.md:80-86`) — this is the single biggest
discovery gap.

### Dependency resolution
Mostly absent. r2pm has dependency *hints* in its scripts; NSE has an inter-script
`dependencies` table; Volatility declares input requirements (not inter-plugin
deps). None does transitive resolution with cycle detection. **Limpet already
exceeds the entire field here**: `limpet/registry_core.py` resolves declared
`[dependencies]` constraints, detects cycles, and emits a topological `start_order`
(`design/wq3810_limpet_plugin_system.md:68-78`). This is a differentiator, not a
gap — preserve it.

### Versioning
Binary Ninja advertises API/platform/Python compatibility; r2pm/Metasploit/Ghidra
carry a version string with no enforced constraint language. Limpet's small total
constraint grammar (`>= > <= < == * any`, `limpet/manifest.py`) matches what
Cargo/apt users expect and is stronger than most of the field. Fine as-is.

### Trust / signing
**This is the field-wide hole.** None of the ten signs or verifies plugin
manifests or code. ClawHub and Binary Ninja mediate trust through a curated
operator; Metasploit mediates through PR review + `msftidy`; everyone else is "run
arbitrary code from a stranger's repo." Limpet's `[source] git`/`rev` already
pins provenance by revision, and wq#3810 lists signing as future work
(`design/wq3810_limpet_plugin_system.md:118-121`). **Signing is where Limpet can
lead rather than follow** — see §3.

### Sandboxing
Universally absent. Every ecosystem runs plugins with the full privileges of the
host tool (IDAPython, Ghidra Java, Volatility/Metasploit/NSE/Frida scripts all
execute in-process or as the user). Frida's gadget isolates the *target*, not the
plugin. Limpet is unusual in that plugins declare *commands* (health/runtime) the
registry runs out-of-process via systemd `--user`
(`design/wq3810_limpet_plugin_system.md:53-66`) rather than loading code
in-process — which means Limpet is one `systemd-run` hardening step away from the
only real plugin sandbox in the field (see §3).

## 3. Recommendations for the Limpet plugin system

Ordered by leverage. Each is scoped against the current design, not a rewrite.

1. **Add a curated central index — a `limpet-plugins` git repo.** Adopt the
   Binary Ninja / r2pm model: one index repo aggregating each plugin's
   `limpet-plugin.toml` metadata (name, kind, version, summary, category,
   `[source] git`/`rev`) into a single `index.json`. Back the already-planned
   `limpet install <git-url>` (wq#3810 future work) with `limpet install <name>`
   and `limpet search <term>` that resolve against the index. This closes the
   discovery gap and reuses the existing `[source]` provenance fields verbatim.

2. **Sign the index entries and verify on install — lead where the field is
   empty.** No competitor signs plugins. Have the index carry a publisher
   identity and a signature over each entry's `(name, git, rev)` tuple
   (minisign/sigstore; stdlib-friendly: ship a detached signature file, verify
   before clone). Trust-on-first-use for a new publisher, pin thereafter. Because
   `rev` is already the install contract, a signature over `rev` gives
   content-addressed integrity without new manifest fields. This turns wq#3810's
   "signature/trust (future work)" into Limpet's headline differentiator.

   **Live-search addendum (wq#3996): the VSCode verified-publisher failure is
   the design's negative example.** The out-of-fleet live-search pass
   (`/home/lumen/scratch/research/limpet_live_search_2026-10-14.md` B3)
   surfaced ox.security's finding that VSCode Marketplace's verified-publisher
   badge was spoofable: the client derived "verified" from a **mutable server
   response**, not from anything bound to the extension's content, and VSCode
   verifies extension signatures only at **install time**, never again. Four
   hard constraints for Limpet's signing design follow:

   1. **Never model trust as a mutable `verified` flag.** Trust must be a
      signature over the content-bound `(name, git, rev)` tuple, exactly as
      specified above — a server (or index repo) compromise can flip a flag
      but cannot forge a signature over a content address. The design as
      written is structurally immune to the VSCode failure; keep it that way
      (no "trusted: true" field in `index.json`, ever).
   2. **Re-verify on load, not just on install.** VSCode's install-time-only
      check means a post-install tamper is invisible. Limpet's registry scan
      (`limpet/registry.py:scan`) should re-verify the pinned `rev` (and the
      signature over it) each time a plugin is loaded. This is cheap precisely
      because `rev` is content-addressed: `git rev-parse HEAD` against the
      pinned value is one subprocess, no network.
   3. **Per-file hash validation inside the pinned tree.** A git `rev` pins
      the tree at clone time, but the working copy can drift afterwards.
      Record per-file hashes (the tree objects git already has) at install and
      validate them at load, so an edited `health` script inside an otherwise
      correctly-pinned checkout is caught.
   4. **Reject unsafe plugin names before writing any byte.** The same
      live-search pass found path-traversal-shaped names in extension tooling
      (varve / export-vsix prior art). Limpet's filename-stem ==
      `[plugin].name` invariant (`limpet/registry.py:scan`) already constrains
      names after the fact; enforce the name grammar (no `/`, no `..`, no
      leading `-`) **before** `limpet install` writes anything to disk, so a
      malicious index entry cannot place files outside the plugin root.

   Mature general-purpose packaging validates the surrounding choices:
   Homebrew (tap = plain git repo, `brew audit` as the CI gate, mandatory
   license field, optional deps) is live prior art for the §3.1 index repo,
   this section's CI-enforced trust gate, and §3.5's soft-dependency
   affordance; Nix's content-addressed derivations are the at-scale proof
   that signing over a content address (here, `rev`) is the durable trust
   anchor (`.../limpet_live_search_2026-10-14.md` PART B).

3. **Harden the plugin sandbox with systemd properties.** Limpet already runs
   plugin health/runtime commands out-of-process under systemd `--user`; add
   hardening properties to that invocation (`NoNewPrivileges=yes`,
   `ProtectSystem=strict`, `ProtectHome=read-only` where feasible, `PrivateTmp=yes`,
   a restricted `ReadWritePaths` to the plugin's own state dir). No other RE tool
   sandboxes plugins at all; Limpet gets the only real isolation story almost for
   free from an architecture choice it already made.

4. **Add a category taxonomy to the manifest and index.** Every strong ecosystem
   (NSE categories, ClawHub/BN marketplace categories) selects by category. Add an
   optional `[plugin] category` field (e.g. `disassembler`, `decompiler`, `fuzzer`,
   `firmware`, `sdr`, `debugger`, `flash-programmer`) drawn from the taxonomy
   already implicit in `limpet/examples/` and `limpet/hardware.d/`, and let
   `limpet search` and the index filter on it. Keep it a closed enum validated in
   `manifest.py` so the index stays browsable.

5. **Keep the dependency resolver and version grammar; add soft deps + conflicts.**
   The resolver already beats the field. The one apt/Cargo affordance worth adding
   is optional/soft dependencies and a `conflicts` declaration, so two plugins that
   claim the same hardware device can be mutually excluded at resolve time rather
   than racing at runtime.

6. **Borrow Lumina's idea — separate the data layer from the code layer.** IDA's
   Lumina (crowd-sourced function names) and Volatility's ISF symbol tables show
   that RE plugins often ship *data* (signatures, device profiles, symbol maps) as
   much as code. Limpet's hardware/target-device TOML catalogs
   (`limpet/hardware.d/`, `limpet/target-devices.d/`) are exactly this layer;
   treat data-only plugins as a first-class `kind` so a HackRF USB-id profile or a
   SoC boot-mode table can be shared and versioned through the same index without
   carrying a runtime command.

## 4. Community contribution workflow design

Three contribution models appear in the survey: **monorepo PR + linter**
(Metasploit — strong review, but a fat shared tree everyone touches),
**index-entry PR, author keeps their own repo** (Binary Ninja community-plugins,
Obsidian-style), and **open dump repo** (radare2-extras, YARA repos — no gate).
The middle model fits Limpet's architecture directly, because a Limpet plugin is
already a standalone git repo with a `limpet-plugin.toml` at its root
(`design/wq3810_limpet_plugin_system.md:80-86`).

Recommended workflow:

1. **Author** writes `limpet-plugin.toml` in their own repo, tags a release
   (`rev`), and runs `limpet validate` locally (already scriptable, exits non-zero
   on a bad manifest — `limpet/cli.py`).
2. **Submit** a PR to the `limpet-plugins` index repo adding one entry: `name`,
   `kind`, `category`, `git`, `rev`, `summary`, publisher key id. No code, just the
   pointer — the author keeps ownership and release cadence of their code.
3. **CI gate** (the Limpet analogue of `msftidy`) clones at the pinned `rev` and
   runs `limpet validate` on the fetched manifest, checks the filename-stem ==
   `[plugin].name` invariant (`limpet/registry.py:scan`), checks the health-command
   shape, verifies the publisher signature (§3.2), and rejects a manifest that
   declares an unresolvable dependency against the rest of the index.
4. **Merge** regenerates `index.json`; `limpet search`/`install` pick it up on the
   next `limpet index update` (the Binary Ninja `plugins.json` refresh pattern).
5. **Categories** from §3.4 are the browse/selection axis; **signatures** from
   §3.2 make the index a trust layer, not just a discovery layer.

This keeps the core lean (no plugin code ever merges into Limpet), gives authors
ownership, and makes the index the single place discovery, versioning, and trust
are enforced — the combination no single surveyed ecosystem achieves today.

## 5. One-line verdict per Limpet choice
- TOML declarative separate manifest → **right** (matches Binary Ninja, the best prior art).
- Dependency resolver + cycle detection + topo order → **ahead of the field**, keep.
- Version constraint grammar → **right-sized** (Cargo/apt parity), keep.
- Discovery (git-repo-per-plugin, no index) → **gap**, fix with a central index (§3.1).
- Trust/signing → **field-wide gap; make it Limpet's lead** (§3.2) — signature over content-bound `(name, git, rev)`, re-verified at load, never a mutable verified flag (VSCode's spoofed badge is the negative example).
- Sandboxing → **nearly free from the systemd architecture already chosen** (§3.3).
