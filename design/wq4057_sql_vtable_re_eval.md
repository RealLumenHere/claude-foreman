# wq#4057 — LIMPET EVALUATE: SQL-virtual-table RE interface (idasql/ghidrasql/bnsql) as agent query layer over Ghidra

Status: research/design. Author: build worker, 2026-10-15. Answers the four questions in
the ticket for the Recon 2026 "SELECT * FROM binary" toolset (Olivier Thomas/Texplained,
2026-06-19), which is the concrete implementation behind `design/wq3929_competitive_analysis.md`
item A6 ("Recon-2026 SQL-based RE", `:194-203`) — that doc judged the *idea* sight-unseen
and recommended "adopt as an optional workspace index, not the primary interface"; this
ticket inspects the *actual tools* and checks whether that stands.

## 0. Grounding — what was verified, and a license finding that reframes everything below

`fleet-search web`/`read` worked this session (SearXNG up, google-cse engine answering;
brave/duckduckgo/startpage individually suspended/CAPTCHA'd, as usual — ISSUE-LOG #1332).
`fleet-search github` is still down (401, no usable token — ISSUE-LOG #1325), so repo
metadata (stars, issues, commit graph) came from rendered GitHub pages via `fleet-search
read`, fetched live 2026-10-15:

- `https://github.com/0xeb/ghidrasql` (README, 21.8KB extracted)
- `https://github.com/allthingsida/idasql` (README, 25.4KB extracted)
- `https://github.com/r0tL/bnsql` (README, 12.4KB extracted)
- `https://github.com/0xeb/libxsql`, `https://github.com/0xeb/idax` (confirmatory snippets, via web search)

**Deliberate scope limit, stated up front:** I did not clone, build, or run any of the
three tools. Two independent reasons, not one excuse:

1. **Environment doctrine** — downloaded archives are untrusted data; running their
   installer/bootstrap scripts (all three ship an "AI-agent install runbook" designed to be
   handed verbatim to a coding agent — see §1) from inside this worktree is exactly the
   pattern the environment's handling rules warn about, and none of the four ticket
   questions requires executing the tool to answer.
2. **The license forbids exactly the move a dissection like `wq4002` makes.** idasql and
   ghidrasql ship under a **custom "Human-Origin Source License v1.0"** (same author, Elias
   Bachaalany / `@0xeb`, across idasql, ghidrasql, libxsql, idax — confirmed by matching
   attribution across all four repo pages). Quoting the ghidrasql README's license summary
   verbatim (fetched 2026-10-15): it prohibits "port, rebrand, clone, API-compatible
   replacement, competing implementation, or **use ghidrasql as AI input to recreate or
   improve a derivative implementation** without prior written permission from Elias
   Bachaalany." That clause is written specifically to block what this agent would
   otherwise do by default (read the source in full, mine its mechanisms, fold them into
   our own plugin). So this eval stays at the black-box/README layer: behavior, interface,
   and license — not source-reading. (It is itself a notable artifact: `r/ProgrammerHumor`
   mocked the license as "justLetAIDoIt" and the author wrote up the rationale on Substack
   — an anti-AI-scraping measure is now showing up in RE tooling specifically because RE
   tools are exactly what agents are being pointed at.)

**Ticket figures corrected against live sources** (same pattern as `wq4002`'s count
correction): the ticket's "30+ tables" is idasql's own figure for *itself*
("exposes 30+ virtual tables" — idasql README) — ghidrasql states **67 tables and 78
views** for itself, and bnsql states **11 virtual tables**. These are three different,
independently-sized schemas, not one combined "30+" surface. The ticket's "272 discrete
MCP tools" for our comparison baseline is also stale: `design/wq4002_bethington_ghidra_mcp_dissection.md:21-29`
(same day, this dissection series) found the live bethington/ghidra-mcp count is **209**,
consolidated down from a historical 272 — use 209, not 272, in any matrix update.

**Authorship note (verification, not nitpicking):** the ticket attributes bnsql to
`github.com/r0tL/bnsql`. That URL does resolve and render a real, substantive README
matching the ticket's description (SQL interface for Binary Ninja, 11 tables, `decompile()`/
HLIL tables, HTTP REST + native MCP server, ~5s startup on a pre-analyzed `.bndb`). But the
same README's own credits section attributes the tool family to "Elias Bachaalany (@0xeb)"
and lists `libxsql` (0xeb's shared SQLite-virtual-table C++ framework, also under the
Human-Origin license) as the library every one of these tools — including bnsql — is built
on, and states bnsql itself is "licensed under the Mozilla Public License 2.0." Net read:
this is the same author's toolset with a shared doc template; `r0tL` is either an org/alias
or a downstream repo carrying the family's boilerplate footer forward. Not fully resolved
(a GitHub profile page didn't render under the fetch tool to confirm `r0tL`'s identity
independently), but the content itself is corroborated, so the architectural claims below
are usable; flag the authorship ambiguity if this ever needs a legal opinion rather than an
engineering one.

## 1. Deliverable 1 — is ghidrasql open + Limpet-license-compatible? **No.**

- **ghidrasql, current releases:** Human-Origin Source License v1.0 — source-available,
  not OSI-approved, not a permissive or standard copyleft license. It explicitly reserves
  the right to block "AI input to recreate or improve a derivative implementation" without
  **prior written permission**. Releases ≤v0.0.2 were MPL-2.0 (now superseded).
- **idasql:** same license, same author, confirmed independently on its own repo page.
- **bnsql:** claims MPL-2.0 for itself (permissive, OSI-approved, Limpet-compatible on
  license grounds alone) — but see §4: platform/maturity rules it out on other grounds
  regardless.
- **Why this matters mechanically, not just legally:** Limpet's own plugin manifest format
  requires a `license` field in `[plugin]` (`limpet/examples/ghidra-mcp.toml:22`,
  `license = "Apache-2.0"` in that example), and the `vetted`/`core` trust tiers require a
  go/no-go audit before a plugin is enabled per workspace or shipped in the base image
  (`design/wq3806_limpet_architecture.md:76-84`, TOOL_REGISTRY preconditions). A license
  that conditions "derivative, AI-informed implementation" on prior written permission is
  precisely the kind of thing a `wq3959`-style license audit is built to catch — this is
  that audit, done at discovery time instead of after the fact.
- **What IS allowed without permission, on the license's own text:** "independent
  implementations that are not copied from, materially derived from, or substantially
  informed by ghidrasql in the license's defined sense are not prohibited." Treating
  ghidrasql as an external black-box CLI/HTTP binary (download the release, shell out to
  it, talk to its HTTP port) is using the tool, not deriving from its source — almost
  certainly fine, same as using any GPL'd CLI without linking against it. **Reading its
  source to copy its virtual-table schema or query-planning tricks into our own plugin is
  the specific thing the license is written to require permission for.** That distinction
  is the actual answer to "is it license-compatible": *compatible as an external dependency
  we shell out to; not compatible as something we mine and reimplement.*

**Verdict: open-source in the colloquial "code is visible" sense, not open-source in the
OSI sense, and explicitly hostile to the one thing an agent-driven competitive dissection
would otherwise do with it.** Treat as `community` tier at best if ever installed (visible,
not invokable without a promotion decision — `design/wq3806_limpet_architecture.md:80-82`),
pending either a relicense or Elias Bachaalany granting written permission, which Limpet has
not sought (opening a permission-request GitHub issue is the kind of physical,
Frank-attributable act the standing autonomy rule reserves for a human — it would be sent
from an account, not from Lumen itself).

## 2. Deliverable 2 — SQL vs. 272 (really 209) discrete MCP tools: complements, doesn't beat, and ships thinner than it looks

**The ergonomic case is real and already conceded.** `design/wq3929_competitive_analysis.md:194-203`
reached this conclusion from the idea alone; the real tools confirm the shape: one query
language, 67-78 (ghidrasql) or 30+ (idasql) or 11 (bnsql) *tables*, versus 209 *tool
schemas* each with its own JSON arg shape the agent must hold in context or page in/out
(`design/wq4002_bethington_ghidra_mcp_dissection.md:70-106`, the lazy-load mechanism that
exists specifically because 209+ tool schemas is expensive/provider-breaking). A `SELECT
name, size FROM funcs ORDER BY size DESC LIMIT 10` is one call an agent already knows how to
write; the MCP-tool equivalent is `list_functions` plus client-side sort/limit, or a
bespoke `get_largest_functions` tool someone had to design, name, and document.

**But two things cut the other way, and together they're the real finding:**

1. **Neither idasql nor ghidrasql speaks MCP natively.** ghidrasql's README has zero
   mentions of "MCP" — it's a CLI (`-q`, `-f`, `-i` REPL) plus an HTTP REST server
   (`--port`, `--bind`, `--auth <token>`) plus a separate "ghidrasql-skills" agent-skills
   pack (prompt-level, not protocol-level). idasql *does* ship a native `--mcp [port]` flag
   — but its own `--help` text says **"MCP has no auth"**, unlike its HTTP mode which
   supports `--token`. bnsql also ships `--mcp` natively. So "SQL as agent query layer"
   today means: idasql ships MCP with no transport auth (mitigated only by the
   `--bind 127.0.0.1` default); ghidrasql ships nothing MCP-shaped and Limpet would have to
   write the `sql_query` MCP tool wrapper itself, proxying to ghidrasql's authenticated HTTP
   server — the safer of the two shapes, and the one criterion 3 below assumes.
2. **One generic `sql_query` tool erases the per-tool trust signal the 209-tool model
   gives for free.** bethington/ghidra-mcp carries `read_only`/`destructive` annotations on
   every individual tool, mapped straight into MCP annotations
   (`design/wq4002_bethington_ghidra_mcp_dissection.md:100-105`). A single `sql_query(sql:
   string)` tool has no such per-call signal — the trust-gate would have to **parse the SQL
   text itself** (statement type: SELECT vs. INSERT/UPDATE/DELETE; which table) to recover
   the same classification a discrete-tool model gets from the tool name alone. That's real
   new engineering surface, and it's exactly where SQL's ergonomic win (one simple language)
   collides with Limpet's trust-gate's security model (per-tool classification at the choke
   point, `design/wq3806_limpet_architecture.md:148-150`). See §4.

**Net:** SQL is a better *read* interface for bulk/structured queries over an already-indexed
program database — this reconfirms wq3929 A6 rather than overturning it. It is not a
drop-in replacement for the tool-plane's write-path trust granularity, and bolting a generic
`sql_query` tool onto a 209-tool plane doesn't remove the 209 tools (an agent still needs
`decompile_function`-shaped tools for actions that aren't "query a table"); it adds a 210th
tool with a different, and currently weaker, trust story.

## 3. Deliverable 3 — ship as a core plugin exposing `sql_query` over headless Ghidra? **Not now. Watch, don't adopt.**

Weighing the pieces above against Limpet's actual plugin bar:

- **License (§1):** blocks treating ghidrasql as anything other than an external dependency
  we shell out to — no reading its source to build our own schema/engine. That alone rules
  out a from-scratch Limpet-native reimplementation "informed by" ghidrasql; it would have
  to be a thin wrapper around the real ghidrasql binary, with all the supply-chain exposure
  that implies (see next point), or an independently-designed schema that happens to query
  the same underlying Ghidra program API without consulting ghidrasql's design.
- **Maturity:** ghidrasql's own repo page is 4 days old at fetch time; this is a brand-new,
  single-maintainer, pre-1.0, no-independent-security-review tool, built on a sibling
  `libghidra` repo that itself needs building from source (C++20, CMake 3.26+, Gradle) — a
  heavier, less-pinned dependency chain than any `core`-tier plugin Limpet ships today.
  Pinned-and-SHA256-verified release downloads (their own claim) are decent hygiene but
  don't substitute for the TOOL_REGISTRY go/no-go (pinned commit/release, setup-script
  source-read in a throwaway env, scoped MCP registration — `TOOL_REGISTRY.md:84-93`-style
  preconditions) that every `vetted` plugin clears first.
- **Dependency coupling:** requires Ghidra 12.1+ and JDK 21 specifically — a version floor
  Limpet's own `ghidra` core plugin would need to already satisfy or be pinned above, adding
  a second version constraint to track alongside whatever `ghidra-mcp` already requires.
- **What it would buy us if adopted anyway:** a materially better bulk-query ergonomic for
  the one class of query wq3929 A6 already flagged as worth having (an indexed, queryable
  view over a large RE output) — a real, not imaginary, benefit.

**Recommendation: SKIP shipping ghidrasql as a plugin this cycle; log it as a `community`-tier
watch item** (visible in the registry concept, not installed, revisit on a relicense or a
1.0/independent-audit milestone — `design/wq3806_limpet_architecture.md:80-82`'s tier exists
for exactly this). **Build the wq3929-A6 per-workspace SQLite RE index independently** —
Limpet's own schema, designed from the Ghidra program API directly rather than read off
ghidrasql's table names, both because the license asks for that separation and because
Limpet's index only needs to serve the workspace-evidence use case (`functions, strings,
xrefs, symbols, tool-invocations`), not full parity with a 67-table/78-view general RE
database.

## 4. Deliverable 4 — write-back safety under the fail-closed trust-gate

The ticket asks specifically about "SQL that mutates the .bndb/ghidra DB." What the tools
themselves do, read off their own docs (black-box, no source read, per §0):

- **ghidrasql** separates "program data that happens to be writable" from "actions." Most
  tables are read-only views; a declared writable subset exists (`program_options`, whose
  `option_scope = 'Analyzers'` rows are explicitly writable and typed) plus one
  action-shaped table (`analysis_passes`: `INSERT` starts a background analysis job, `DELETE
  ... WHERE state = 'running'` cancels it). Persistence is **not automatic**: "Managed
  headless sessions save once on normal shutdown by default. Mutations remain in the live
  Ghidra project until that shutdown; `save_database()` is an optional operator-requested
  checkpoint... No mutation path calls it automatically." `--shutdown discard`,
  `discard_changes()`, and `--readonly` mode all explicitly roll back pending changes rather
  than committing them. This is a genuinely reasonable default-closed posture — closer to
  Limpet's own fail-closed instinct than to an auto-commit model.
- **idasql** defaults to the same shape from the other direction: `-w, --write` is required
  to **persist** changes on exit at all; without it, changes are implicitly discarded.
  `--token` is supported for its HTTP mode but its own help text states flatly **"MCP has no
  auth"** for `--mcp` mode — a real gap if that server is ever bound beyond `127.0.0.1`.
- **bnsql** supports `--token` bearer auth on its HTTP server identically to ghidrasql.

**The structural problem for Limpet is not whether these tools are careless — they aren't —
it's that a generic `sql_query` tool defeats per-call trust classification at exactly the
choke point Limpet relies on** (`design/wq3806_limpet_architecture.md:148-150`, §2 above). A
HAL capability with "no classification, or an ambiguous one, defaults to `energizing` (fail
closed)" (`design/wq3806_limpet_architecture.md:223`) — the same principle applied here
means: **a Limpet `sql_query` tool must treat every statement as mutating until proven
otherwise**, not the reverse. Concretely, if this is ever built:

1. Parse the statement (not trust the caller's claim) to classify `SELECT`/read-only views
   as autonomous, and route any `INSERT`/`UPDATE`/`DELETE`, or a `SELECT` against a
   known-action table (`analysis_passes`), through the same operator-confirm path HAL
   destructive actions use — never auto-approve a write because "it's just SQL."
2. Default the underlying session to the tools' own safest mode: ghidrasql `--readonly` /
   idasql without `--write`, so an uncaught parser gap fails closed (discarded on shutdown)
   rather than fails open (silently persisted).
3. Never bind either tool's bare MCP mode beyond loopback given idasql's documented
   no-auth gap; if adopted, front it with the HTTP+`--token` mode instead and have Limpet's
   own meta-server hold the real credential, matching the masking/credential-handling
   posture used everywhere else in this environment.
4. Treat `save_database()`/`-w`/`--shutdown save` itself as the one truly destructive call
   in the whole surface (it's the only thing that turns transient session state into a
   persisted program change) and gate it specifically, rather than gating every `UPDATE`
   individually and missing the commit step.

This is design guidance for *if and when* a SQL-query tool is built (§3's own
recommendation is to not build one this cycle), not an implementation — no code changes
accompany this ticket.

## 5. Summary / adopt-skip

| Question | Answer |
|---|---|
| (1) ghidrasql open + license-compatible? | No — Human-Origin Source License v1.0, non-OSI, requires written permission to use as "AI input" for a derivative. Usable only as an external black-box dependency, not as something to read/mine. |
| (2) beats or complements the ghidra-mcp tool-plane? | Complements for bulk/structured reads (confirms wq3929 A6); does not replace write-path tooling, and a generic `sql_query` tool is a net new trust-classification problem versus 209 annotated discrete tools. |
| (3) ship as core plugin now? | Skip. Log as `community`-tier watch (license + maturity both fail the `vetted`/`core` bar today); build Limpet's own workspace SQLite index independently of ghidrasql's schema. |
| (4) write-back safety? | The tools themselves default fail-closed (no auto-persist); Limpet's gap would be trust-gate granularity — a `sql_query` tool must SQL-parse and classify per statement, defaulting unclassified/write statements to the operator-confirm path, exactly as unclassified HAL capability defaults to `energizing`. |

**Overall: SKIP adoption this cycle.** The thesis ("SQL is a language every agent already
speaks") is sound and already reflected in Limpet's own roadmap (wq3929 A6); the specific
tools that prove the thesis are too new, too license-encumbered, and structurally
mismatched to Limpet's per-call trust-gate to adopt directly. Revisit if either (a) ghidrasql
relicenses or grants written permission and clears a `vetted`-tier go/no-go, or (b) Limpet's
own meta-server grows a generic-tool SQL-statement classifier for other reasons, at which
point wiring ghidrasql behind it becomes a much smaller lift than it is today.

Relates: `design/wq3929_competitive_analysis.md` item A6/(8) (the idea, pre-tooling),
`design/wq4002_bethington_ghidra_mcp_dissection.md` (209-tool correction, lazy-load/trust
patterns reused in §2/§4), `ops/wq3808-limpet-mcp-meta-server-a1/` (where a `sql_query`
wrapper or statement classifier would live if ever built), `design/wq3810_limpet_plugin_system.md`
and `limpet/examples/ghidra-mcp.toml` (manifest shape a ghidrasql plugin would need).
