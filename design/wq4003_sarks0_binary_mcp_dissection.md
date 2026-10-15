# wq#4003 — LIMPET DISSECT: Sarks0/binary-mcp (cross-conversation SHA256 session model + static/dynamic toolset-class)

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/Sarks0/binary-mcp` per the competitor dissection guide §2.4
(`scratch/research/limpet_competitor_dissection_guide.md:263-275`). Fifth in the Tier-1/Tier-2
dissection series: wq3998 set the tool-breadth bar, wq3999 the methodology + sha256-workspace
bar, wq4000 the code-critic bar, wq4002 the lazy-load/schema-budget bar; this one dissects the
only competitor that **persists analysis across conversations keyed by binary content** and that
**pairs a static engine with a live debugger in one server** — the two capabilities the ticket
asks Limpet to extract.

## 0. Grounding (live, not memory) — and two scope corrections

Cloned `--depth 1` into `/tmp/fworker-wq4003/binary-mcp-src` and read directly this session, as
untrusted data (no instruction inside the target tree was acted on). Citations are
`BMCP:<path>:<line>`. Prior-art web search was attempted and is down fleet-wide this session
(`fleet-search web`: all SearXNG general engines suspended/CAPTCHA, no `BRAVE_API_KEY` — the known
wq#2595 / ISSUE-LOG #1332 condition wq4002 also hit); the primary source for a dissection is the
cloned tree itself, read in full where load-bearing.

**License (for the wq3959 audit):** Apache-2.0 (`BMCP:LICENSE:1-2`). Adoptable as a `vetted`
plugin or wrappable; no license obstacle.

**Scope correction 1 — the dynamic side is no longer x64dbg-only.** The guide records "dynamic
(14 tools, x64dbg)". Live tree carries **two** dynamic engines: `x64dbg` *and* `windbg`
(`BMCP:src/engines/dynamic/` holds `x64dbg/` and `windbg/`, the WinDbg bridge alone is 68 KB and
has a kernel-debug transport layer). The debugger tool category is documented as "Live debugging
via **x64dbg or WinDbg** against a running target or dump" (`BMCP:src/tool_catalog.py:70`). The
static side likewise grew past Ghidra: `ghidra/`, `dotnet/` (ILSpy for .NET/CIL) and a native
`python/` analyzer (`BMCP:src/engines/static/`). So the "static(Ghidra)+dynamic(x64dbg)" pairing
the ticket names is live as a **multi-engine** static+dynamic pairing; the design lesson is the
pairing *mechanism*, which generalises past the two engines the guide named.

**Scope correction 2 — SHA256 is the key at the *cache/debug-state* layer, a *correlation* key at
the *session* layer.** The guide says "key = SHA256 of the binary". Precisely: the Ghidra analysis
cache is content-addressed — cache files are literally `<sha256>.json.gz`
(`BMCP:src/engines/static/ghidra/project_cache.py:83-97`, hash computed+memoized by
`_get_binary_hash` `:111-135`) — and x64dbg debug-state IDs are "hex SHA256 prefixes"
(`BMCP:src/utils/security.py:852-875`). But a **session** is keyed by a freshly minted
`uuid.uuid4()` (`BMCP:src/engines/session/unified_session.py:304`), with the binary's SHA256 stored
as a `binary_hash` field used to *correlate and auto-resume* sessions, not to name their files.
That distinction is the whole substance of deliverable 1 and is where Limpet must not copy naively.

## 1. Deliverable 1 — extraction spec for the cross-conversation SHA256-keyed session model

### 1a. Exactly what binary-mcp does (read from `unified_session.py`)

- **Store.** Default `~/.binary_mcp_sessions`, overridable by `$BINARY_MCP_SESSION_DIR`
  (`BMCP:...unified_session.py:118-127`). Two files per session: `<uuid>.session.json.gz` (gzip,
  the full tool-call log) and `<uuid>.meta.json` (uncompressed lightweight summary)
  (`BMCP:...unified_session.py:139-164`, written by `save_session` `:457-498`).
- **Identity vs correlation.** `session_id = uuid4()` (`:304`); `binary_hash = sha256(file)`
  streamed in 8 KB chunks (`_compute_binary_hash :166-177`) lives in both the session blob and the
  metadata. Every lookup that spans conversations keys on `binary_hash`, never on the filename.
- **Cross-conversation continuity is two mechanisms, not one:**
  1. **Auto-resume within a window.** `ensure_session(binary_path, analysis_type)` hashes the file,
     then `_find_recent_session_for_binary` globs `*.meta.json`, keeps entries whose `binary_hash`
     matches, `status in {active,saved}`, and `updated_at` is newer than
     `SESSION_RESUME_WINDOW_SECONDS = 3600`, and resumes the most recent
     (`:179-227`, `:229-278`, window const `:103`). So re-opening the *same bytes* within an hour —
     even in a brand-new conversation — silently continues the prior session's tool-call log.
  2. **Unbounded manual lookup.** `find_sessions_for_binary(binary_path|binary_hash)` returns *all*
     sessions for a hash regardless of age (`:633-670`); the agent then resumes one explicitly.
- **Progressive retrieval (the `load_session_section` the ticket names).** `get_section` serves
  `metadata | summary | static_tools | dynamic_tools | tools`, each optionally filtered by tool
  name or analysis type (`:556-631`); the MCP tool `load_session_section` is the thin wrapper
  (`BMCP:src/server.py:5272-5368`) and `get_session_summary` prints the exact next-step calls to
  pull each slice (`BMCP:src/server.py:5254-5258`). This is the same "small core, load the slice you
  need on demand" discipline REx uses for references — applied to *stored evidence* so a resumed
  conversation never has to reload the whole (gzipped, possibly multi-MB) log to see what ran.
- **Auto-logging.** A `@log_to_session(analysis_type=...)` decorator wraps every analysis tool;
  it auto-opens/resumes the session from `binary_path` and appends `{timestamp, tool_name,
  arguments, output, output_size, analysis_type}` (`BMCP:src/server.py:164-229`,
  `log_tool_call :362-415`). Replaying a session is replaying that list.

### 1b. The two sharp edges Limpet must not inherit

1. **No index — O(n) glob on every lookup.** There is *no* `index.json`; both
   `_find_recent_session_for_binary` and `find_sessions_for_binary` open and `json.load` **every**
   `*.meta.json` in the store on each call (`:194`, `:657`). REx ships `results/index.json`
   mapping sha256→dir (`design/wq3999_rex_skill_dissection.md:55`) precisely to avoid this; Limpet's
   workspace spec already adopted an index (`wq3999:69`). **Keep the index; do not copy the glob.**
2. **Path-hash fallback silently breaks correlation.** When the file can't be opened (dynamic
   analysis of a remote/attached target), `_compute_binary_hash` falls back to
   `sha256(binary_path.encode())` (`:174-177`) — a hash of the *path string*, not the *content*. A
   dynamic session keyed on the path-hash will **not** correlate with the static session that
   content-hashed the same bytes, so the "one target, one session across static+dynamic" promise
   quietly fails exactly when static and dynamic disagree on how they name the target. Limpet, whose
   HAL can attach to a device with no local file, must decide the content key *once* at ingest and
   carry it, never recompute it per-engine with a per-engine fallback.

### 1c. Extraction spec (ADOPT, aligns REx@Skill sha256 workspace + Limpet git workspace)

The Limpet workspace spec already took the sha256 dir-name + `index.json` from REx
(`wq3999 §2`, extends `wq3806:238-245`). binary-mcp contributes the **session/replay layer that
sits inside that content-addressed workspace**:

1. **Content key decided once at ingest, not per-engine.** On first ingest, compute the artifact's
   SHA256 and record it in `manifest.json`; the workspace dir is `<basename>-<sha256[:8]>` per
   wq3999. Every engine (static *and* dynamic/HAL) correlates against that stored key — fix edge
   (2) by never letting a later engine recompute-with-fallback. For a device/stream with no file,
   the key is the HAL capture digest, assigned at capture, not a path string.
2. **A `sessions/` subdir inside the workspace, UUID-named, SHA256-correlated via the existing
   index.** Keep binary-mcp's split of a compressed full log + an uncompressed lightweight meta
   (cheap to scan), but replace its glob with an entry in the workspace `index.json`
   (`binary_hash → [session_uuid,…]`). Because the workspace dir is *already* the content key, this
   "correlation" collapses to "list the sessions under this workspace" — binary-mcp's cross-store
   glob is only needed because its sessions are a flat pool divorced from any workspace; Limpet's
   git-backed per-target workspace makes the pool local and ordered for free.
3. **`load_session_section`-equivalent as the replay read.** Adopt the section menu
   (`metadata | summary | static | dynamic | tools[tool_filter]`) verbatim as the workspace replay
   API, and adopt `get_session_summary`'s "here are the exact next-step calls" affordance — it is
   the progressive-context pattern applied to evidence and keeps a resumed run from reloading the
   whole log. This composes with the git step-log (`wq3806:243`): git history is *what changed*,
   the session log is *what the agent did*, both under the one content key.
4. **The auto-resume window is a defaulted-continuity policy, adopt it as policy not magic.**
   binary-mcp's silent 1-hour auto-resume is convenient but can graft a new conversation's calls
   onto a stale session without the operator noticing. Limpet should resume explicitly on
   workspace-open (the workspace *is* the durable key, so there is no need to time-box it), and log
   the resume as a step. Keep the content-hash match; drop the invisible time window.
5. **Carry binary-mcp's two security hardenings into the session layer (they are directly on-point
   for an agent that stores attacker-controlled bytes — the wq3999 §3 quarantine theme):**
   - **ID validation at a chokepoint.** Session IDs are pinned to canonical UUIDv4 and validated in
     the *one* path-building method every read/write/unlink funnels through, not at each of ~8 tool
     entry points (`_validate_session_id :37-80`, chokepoint `_get_session_path :139-152`), with the
     same shape reused for debug-state IDs (`security.py:852-875`). This closed a real path-traversal
     (audit F-5): `../../tmp/x` as a session_id had escaped the store. Limpet's workspace/evidence
     IDs must get the same single-chokepoint validator.
   - **Store content, re-fence on read.** Stored tool output has its untrusted-content envelope
     *stripped* before persistence and *re-applied* on every replay (`log_to_session` strips
     `:219-223`; `load_session_section` re-wraps `wrap_untrusted`/`strip_untrusted_envelope`
     `:5321,5357`), so replaying a session can never let a sample's bytes arrive unfenced and never
     nests envelopes. This is the concrete mechanism behind wq3999's "quarantine is the single most
     important import": adopt strip-on-write/fence-on-read for the Limpet evidence replay path.
   - **Confine before you hash (audit F-8).** binary-mcp's own fix: `ensure_session` used to open +
     hash + persist the path *before* the tool body's confinement check, so a decorated call on
     `/etc/shadow` read, hashed and recorded it before refusing (`BMCP:src/server.py:184-205`). The
     ordering invariant — **confine the path before any read/hash/persist** — is a Limpet ingest
     invariant, not just a binary-mcp bug note.

## 2. Deliverable 2 — the static(Ghidra)+dynamic(x64dbg/WinDbg) pairing as a Limpet toolset-class

### 2a. How binary-mcp pairs them in one server

- **The pairing lives in one decorator, not two servers.** `@log_to_session(analysis_type=STATIC)`
  vs `@log_to_session(analysis_type=DYNAMIC)` is the only thing that classes a tool static or
  dynamic (`BMCP:src/server.py:164-229`; the manager also infers it from an `x64dbg_` name prefix as
  a fallback, `unified_session.py:386-391`). Both classes write to the **same** session log.
- **A session is promoted to `mixed` the moment both appear.** `AnalysisType` ∈
  `{static, dynamic, kernel, mixed}` (`unified_session.py:83-88`); `ensure_session`/`log_tool_call`
  flip a static or dynamic session to `mixed` when a call of the other class arrives
  (`:247-252`, `:405-408`). So "analyse statically in Ghidra, then attach x64dbg/WinDbg to the same
  binary" is one continuous, content-correlated session whose metadata counts static vs dynamic
  calls separately (`static_tool_count`/`dynamic_tool_count`, `save_session :473-491`) and whose
  replay can be sliced to either class (`load_session_section('…','static_tools'|'dynamic_tools')`).
- **The toolset taxonomy is tags, not a menu.** `tool_catalog.py` assigns every tool exactly one
  `category:` plus zero or more facets, applied as FastMCP tags (`BMCP:src/tool_catalog.py:12-13,
  96-106`). Categories (`:37-71`): `decompile, review, analysis-run, census, detection, triage,
  annotation, coverage, session, reporting, jobs, admin, threat-intel, debugger` — the whole static
  surface is the first several; the whole live-analysis surface is the single `debugger` category
  ("x64dbg or WinDbg", `:70`). Facets (`:73-93`) are orthogonal trust/cost flags —
  `code-output` ("gate on this to keep code out of the model's context"), `runs-engine` ("may launch
  Ghidra headless … can take minutes"), `network` ("always contacts VirusTotal/a symbol server").
  This is a *finer* cut than a static/dynamic binary: trust and cost are their own axis.

### 2b. Extraction spec (ADOPT as a Limpet toolset-class contract)

1. **Make "toolset-class" a first-class tag with an analysis-phase axis AND an orthogonal
   trust/cost facet axis.** Limpet's two-level meta-server (wq3806 §; validated in wq4002 §2) routes
   by target-class (firmware vs APK vs web); binary-mcp shows the *within-target* cut the router
   still needs: `phase ∈ {static, dynamic, kernel}` × `facet ∈ {code-output, runs-engine, network,
   mutating}`. Adopt the facet set near-verbatim — `code-output` and `runs-engine` map straight onto
   Limpet's existing "keep decompiled code out of context" and "slow/engine-launching" concerns, and
   `network` is the egress flag the trust-gate already needs. `mutating` is Limpet's addition
   (binary-mcp's debugger is largely read/observe; a patch/repack plugin per wq4015 needs it).
2. **One session spans the pair; class is metadata on each call, not a session type.** Adopt the
   `mixed`-promotion model: a Limpet workspace session does not pre-declare static-or-dynamic; each
   logged step carries its phase, and the session's phase is the union. This is what lets a HAL
   dynamic trace and a Ghidra static finding sit in one replayable, content-keyed evidence log —
   the exact composition Limpet wants between its git workspace (deliverable 1) and its HAL.
3. **The debugger category is the template for the HAL/dynamic toolset-class Limpet lacks.** Every
   other dissected competitor is static-only (wq3998/3999/4000/4002 all note no/weak dynamic);
   binary-mcp is the one with a shipped live-analysis surface, and it is *one category* behind a
   command gate (x64dbg command allowlist + `validate_state_id` SHA256-prefix state keys,
   `tests/test_x64dbg_command_gate.py`, `security.py:852-875`). Limpet's dynamic/HAL toolset-class
   should copy the shape: a single phase=dynamic class, every command allowlisted at a gate, every
   live-state handle keyed to the target's content hash so a dynamic step is auditable against the
   same key as the static evidence. The *engines* behind it are Limpet's (QEMU/HAL/Frida per the
   firmware-emulation skill), not x64dbg/WinDbg — but the class contract is binary-mcp's.
4. **Keep facets as a real gate, not advisory tags.** binary-mcp's facets are documentation + test
   fixtures (`tests/test_tool_catalog.py` fails on an untagged tool); wq4002 already found Limpet's
   edge is *enforcing* trust tiers where bethington only annotates. Same verdict here: adopt the
   taxonomy, enforce `network`/`mutating`/`runs-engine` at the meta-server choke point rather than
   trusting the tag.

## 3. Net differentiation read

Sarks0/binary-mcp is the field's reference for two things Limpet specifically wants and no other
dissected peer has both of: (a) analysis that **survives a conversation boundary** because it is
correlated by the binary's content hash, and (b) a **static engine and a live debugger in one
replayable session**. Neither requires new Limpet primitives — both land inside the already-adopted
sha256 content-addressed, git-backed workspace (wq3999 §2) as a `sessions/` replay layer and a
`phase × facet` tag contract. The two bugs binary-mcp fixed in flight (confine-before-hash ordering,
UUID chokepoint validation) and the two edges it still has (no index → O(n) glob; path-hash fallback
breaks static↔dynamic correlation) are the concrete do/don't list for the Limpet implementation.
Roadmap deltas: add the `sessions/` replay layer + `load_session_section`-equivalent to the
workspace; add a `phase` axis and the `code-output/runs-engine/network/mutating` facets to the
meta-server router and enforce them at the choke point; use the debugger category as the template
for Limpet's missing dynamic/HAL toolset-class. None of it moves the moat (HAL + appliance +
trust-gate); all of it is table-stakes continuity + a dynamic surface the competitor proves is
cheap to class once the session model carries it.
