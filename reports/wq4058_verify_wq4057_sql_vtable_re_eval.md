# wq#4058 — verify-pass on wq#4057 (LIMPET: SQL-virtual-table RE interface eval)

Archetype 8 adversarial verify. Author: claude-sonnet-5 (fworker-wq4057). Verifier: claude-sonnet-5@high,
fresh instance (fworker-wq4058). PR #2008 (`a1d6ff162b`), single file:
`design/wq4057_sql_vtable_re_eval.md` (256 lines, docs-only — no code, confirmed via
`git show --stat a1d6ff162b`).

## Method

This ticket is a research/eval doc, not code — there is no unit-test probe. The adversarial
"probe" here is independent re-verification of every checkable factual claim against live
primary sources, plus a read for internal logical consistency and citation accuracy. For each
category below I re-fetched the source myself (not trusting the PR's own quotes) via
`fleet-search web`/`fleet-search read` and compared byte-for-byte where the doc claims a quote.

## What was checked and confirmed

- **License finding (the load-bearing claim).** Re-fetched `github.com/0xeb/ghidrasql` live.
  The "Human-Origin Source License v1.0" clause — "You may not maintain a divergent private
  fork, port, rebrand, clone, API-compatible replacement, competing implementation, or use
  ghidrasql as AI input to recreate or improve a derivative implementation without prior
  written permission from Elias Bachaalany" — matches the doc's quote verbatim. The
  `r/ProgrammerHumor` "justLetAIDoIt" reference and Elias Bachaalany / `@0xeb` authorship across
  idasql/ghidrasql/libxsql/idax also confirmed independently via search.
- **Table/view counts.** ghidrasql README states "67 public tables and 78 views" (exact match);
  idasql README states "30+ virtual tables" for itself (exact match, and correctly attributed
  as idasql's own figure, not a combined one); bnsql README states "11 Virtual Tables" (exact
  match). The doc's correction of the ticket's conflated "30+" figure is justified.
- **bnsql authorship/license ambiguity (§0).** Fetched `github.com/r0tL/bnsql` directly: its
  credits section does attribute the family to "Elias Bachaalany (@0xeb)", lists `libxsql` as
  the shared framework, and states "This project is licensed under the Mozilla Public License
  2.0" — all three sub-claims match the doc's text precisely, including the doc's own
  hedge that `r0tL`'s identity wasn't independently confirmed.
- **CLI/flag claims.** idasql `--help` text: `--token <token> Auth token for HTTP server mode
  (MCP has no auth)` and `-w, --write Save database on exit (persist changes)` — both quoted
  accurately. ghidrasql: `--readonly`, `--port`/`--bind`/`--auth <token>`, `program_options
  (writable)`, `analysis_passes (INSERT starts analysis)` all present verbatim in the live
  README's table listing. bnsql `--mcp` flag for native MCP server confirmed.
- **Dependency chain.** ghidrasql README confirms "Ghidra distribution (12.1+) and JDK 21",
  `libghidra`, "C++20 compiler ... CMake 3.26+ ... Gradle" as the from-source build path — the
  doc fairly also doesn't hide that a pre-built CI release zip avoids this chain, it just
  correctly notes the *source* path is heavier than Limpet's current bar.
  (It does gloss "a sibling `libghidra` repo that itself needs building from source" as if
  always required — technically the pre-built release path skips that; a nit, not an error,
  since the doc is evaluating the worst case a `vetted`-tier audit would have to clear.)
- **Internal citations.** Spot-checked every `file:line` reference used as supporting
  evidence: `design/wq3929_competitive_analysis.md:194-203` (A6 recommendation, matches),
  `design/wq4002_bethington_ghidra_mcp_dissection.md` 209-tool/272 correction (matches),
  `design/wq3806_limpet_architecture.md` tier definitions (~80-84), meta-server trust
  enforcement (148-150), and `energizing`/fail-closed default (223) — all match the actual
  file content. `limpet/examples/ghidra-mcp.toml:22` — line 22 is literally
  `license = "Apache-2.0"`, exact match. `TOOL_REGISTRY.md:84-93`-style preconditions
  (pin commit/release, source-read setup script, scope MCP registration) match the three
  numbered items at lines ~84-93 of that file.
- **ISSUE-LOG references.** #1332 and #1325 both exist and describe fleet-search
  web/github outage states consistent with how the doc uses them.
- **No code shipped.** Confirmed via `git show --stat` — one new file under `design/`, no
  touches to `limpet/` or anything executable, consistent with the ticket's own closing line
  ("no code changes accompany this ticket").

## Defect found

**§2, point 1 — self-contradicting topic sentence.** The doc states: *"Neither idasql nor
ghidrasql speaks MCP natively."* The next two sentences in the same bullet immediately
contradict this: *"idasql **does** ship a native `--mcp [port]` flag — but its own `--help`
text says 'MCP has no auth'... bnsql also ships `--mcp` natively."* Re-fetching idasql's
`--help` text independently confirms idasql does list a native `--mcp [port]` flag. So the
bolded claim is factually wrong as written (it's true only for ghidrasql, not idasql or
bnsql) and is contradicted by the author's own very next clause — reads like a dropped
qualifier ("...natively **with auth**" was probably intended).

**Impact assessment:** contained, not propagating. The error sits in one topic sentence; the
correct, specific facts (idasql has `--mcp` but no auth; ghidrasql has no native MCP at all;
bnsql has `--mcp`) are stated correctly in the same paragraph and are what the rest of §2 and
§4's design guidance actually build on. The summary table in §5 doesn't restate the wrong
framing. The adopt/skip recommendation, the license verdict, and the write-back safety
guidance are all unaffected by this sentence being wrong — it's a correctable editing defect,
not a finding that undermines any of the ticket's four deliverable answers.

## Verdict: CONFIRM

wq#4057's eval doc is accurate against live primary sources on every checkable fact —
license text, table/view counts, CLI flags, authorship, persistence/write-back semantics,
dependency chain, and every internal `file:line` citation tested matched on re-fetch, several
to verbatim quotes. The adopt/skip recommendation (SKIP ghidrasql/idasql/bnsql this cycle;
build Limpet's own SQLite workspace index; log as `community`-tier watch) is well-supported
by the evidence it cites. One real but non-propagating defect found and documented above
(§2 point 1 self-contradiction on idasql's native MCP support) — recommend a follow-up
one-line doc fix, not a re-open of the ticket's substance. No code shipped, so no
functional/security regression surface to probe beyond the doc's own factual claims.

Confirmed on the board via `wq done`.
