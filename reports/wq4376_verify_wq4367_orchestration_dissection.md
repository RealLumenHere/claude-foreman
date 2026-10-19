# wq#4376 — verify wq#4367 (PR#2255, LIMPET RESEARCH: control-plane orchestrators)

**Verdict: CONFIRM.** Every falsifiable claim in `research/WQ4367_ORCHESTRATION_DISSECTION.md`
holds under a fresh adversarial pass against live, re-fetched primary sources. No
fabricated quote, stat, or citation found.

## What I re-checked, independent of the author's report

Fetched PR#2255's head directly (`git fetch origin pull/2255/head`, commit `d5ec8955`,
one file: `research/WQ4367_ORCHESTRATION_DISSECTION.md`, 335 lines) rather than trusting
the ticket's own notes about what it contains.

### 1. Internal repo citations — all verified against the actual files, not the report's prose

| Claim | Cited location | Verified |
|---|---|---|
| "identify → choose toolset → orchestrate (extract→static→emulate/debug→verify) → write evidence → report" | `design/wq3806_limpet_architecture.md:271-274` | Exact text present at those lines |
| Energize-confirm boundary, unconditional regardless of trust/success | `design/wq3806_limpet_architecture.md:294-302` | Exact text present |
| Obligation ledger / `obligations.require_done` | `limpet/obligations.py`, wq#3965 | File exists; §6 text at line 275 cites wq#3965 for the same mechanism |
| `critic.py` propose/critic/adjudicate | `design/wq3806_limpet_architecture.md:281-289` | Exact text present, matches report's paraphrase |
| `binary_scrub`/`injection` one-source-of-truth | `limpet/DESIGN-DECISIONS.md` wq#4108 D1, lines 38-44 | Exact text present at those lines: "deliberately reuses `injection` for phrasing rather than shipping a second, drifting pattern list — one source of truth for 'what counts as an injection'" |
| Build sandbox has no egress (wq#2595) | `limpet/DESIGN-DECISIONS.md` wq#4086 D3 | Exact text present: "The build sandbox has no egress (fleet-wide, wq#2595)" |
| `sanitize.py`'s open gap, DESIGN-DECISIONS.md lines 32-34 | wq#4127 "Known remaining gap" | Exact text present; `limpet/sanitize.py:201` has `sanitize_workspace` |
| NanoClaw §2 (microVM escalation), §3 (OneCLI vault) | `research/WQ4348_NANOCLAW_DISSECTION.md` | §2 header is literally "MicroVM-based Docker Sandboxes"; §3 "Hardening layers" contains "Credential isolation (OneCLI Agent Vault)" |

Every internal citation I checked resolved to the exact line range claimed, with text
matching the report's paraphrase or verbatim quote. No stale-reference or line-drift
pattern (the recurring class this fleet's verify-passes most often catch, e.g. wq#1308,
wq#4313) is present here.

### 2. External sourcing — fetched live, not taken on the report's word

The report's own sourcing note says SearXNG's engines were suspended/CAPTCHA'd this run
and names three specific primary-source fetches. I re-fetched all three independently:

- **sheath, PyPI JSON API** (`curl https://pypi.org/pypi/sheath/json`, HTTP 200, 57451
  bytes, `version: 0.2.0`, `summary: "Data-integration agent harness on Microsoft Agent
  Framework"`). Checked every specific term the report attributes to this source against
  the raw `description` field: `spill`, `result_parser`, `spill_threshold_bytes`, `8192`,
  `max_spill_bytes`, `100 MiB`, `Microsoft Agent Framework`, `safe_path`, `run_python`,
  `SandboxQuarantinedError`, `NoSandboxIsolationWarning`, `169.254.169.254`,
  `100.64.0.0/10` — **all present**, exactly as cited.
- **Captain Claw, GitHub README** (`curl
  raw.githubusercontent.com/kstevica/captain-claw/main/README.md`, HTTP 200, 79287
  bytes). Checked every quote and stat:
  - "6 orchestration modes · 51 built-in tools per agent · 6 shared memory layers" —
    present verbatim (report hedges "44-51... count varies by version," a reasonable
    hedge given this is a live repo).
  - Plan Mode quote ("A new toggle decomposes a task into ordered steps, drives each to
    a verified result before the next, re-plans the remainder when a step can't be
    verified, and synthesizes from the verified steps — so the system never compounds an
    unverified step") — present verbatim, modulo the README's own markdown bold markers
    around individual words (which the report's prose quote correctly strips).
  - Orchestrate/DAG mode (`/orchestrate`, task DAG, configurable parallel workers, shared
    workspace, JSON Schema + auto-retry, Gantt-style trace timeline,
    `captain-claw-orchestrate` headless entry point) — present verbatim.
  - Code pipeline (three parallel reviewers — correctness/CVSS security/QA that executes
    tests — triage judge acting on blocking/major only, capped fix loop, delta review
    from round 2, backlog persistence at the cap) — present verbatim.
  - Autonomous Work loop (arbiter, Bayesian reliability weight, trust ladder,
    decompose-execute-verify with replan-on-failure, grounded verification) — present
    verbatim.
  - **One naming looseness, not a factual error:** the report labels the 0.8.0 feature
    "Bat mode"; the README names it "**Bat** (0.8.0)". The quote the report attributes
    to it ("a durable 'stubborn finisher': plans, runs checkpointed steps with retries,
    survives restarts, and stops only when an independent judge says the task is done —
    behind a hard safety floor and a start gate for email, spending and accounts") is
    **exact**, word for word. Cosmetic only; does not change the report's verdict.
  - **The report's own sourcing caveat checked out too:** it explicitly says it could
    *not* confirm the ticket text's "default 2 revisions" / "schema then judge" framing
    for Plan Mode specifically, and flags that as unconfirmed rather than inventing a
    citation. I searched the README for "2 revisions," "default 2," "revision," "schema
    then," "schema, then" — **none present**. The author's hedge is itself accurate; it
    under-claims rather than over-claims.
- **OpenAI Symphony, blog post** (`openai.com/index/open-source-codex-orchestration-symphony`,
  fetched via `fleet-search read`, HTTP 200, cached at `2026-10-19T04:25:23Z` — i.e. the
  same fetch the author made, not a new one, but independently re-pulled and re-checked
  term-by-term rather than trusted). Direct fetch via `curl` got HTTP 403 (Cloudflare bot
  block), confirming why the author needed the `fleet-search read` path in the first
  place — not a contradiction, corroborating evidence for the report's own sourcing
  note. Checked:
  - Byline "By Alex Kotliarskyi, Victor Zhu, and Zach Brock" — exact.
  - "garnering over 15K GitHub stars... as of April 23" — exact, under "Community
    shoutouts" as the report claims.
  - "giving agents objectives instead of strict transitions, much like a good manager
    would assign a goal to a direct report on their team" — the "good manager/direct
    report" clause is an exact quote; "objectives-not-transitions" is the report's own
    compressed heading for that idea, not a separately-sourced claim — acceptable
    paraphrase, not a fabrication.
  - "If an agent crashes or stalls, Symphony restarts it" — exact.
  - "Agents only start working on tasks that aren't blocked... we marked the React
    upgrade as blocked on a migration to Vite" — exact.
  - "to avoid exposing the Linear access token to subagents, we use dynamic tool calls to
    expose the raw `linear_graphql` function... without relying on MCP or exposing the
    access token to containers" — exact, matches the report's NanoClaw-OneCLI comparison.
  - "500% increase in landed pull requests" and "Linear founder... highlighted a spike in
    workspaces created" — both exact (report correctly does not name the founder; post
    does: Karri Saarinen — report's omission is not an error, just less detail).

## Things I tried to break and couldn't

- Tried to catch an invented quote dressed as verbatim — every blockquoted sentence in
  the report traces to an exact (modulo markdown bold stripping) string in the live
  source.
- Tried to catch a stale/incorrect internal line citation (the recurring defect class in
  this fleet's prior verify-passes) — all eight internal citations checked resolved to
  the exact lines claimed.
- Tried to catch the report over-claiming precision it didn't have — found the opposite:
  the one place the author flagged uncertainty (Plan Mode's "default 2 revisions"/
  "schema-then-judge" framing from the ticket text) is a place where the literal string
  genuinely isn't in the source, so the hedge is correct, not cover for a guess.
- Tried the Linear-token/MCP claim as a plausible embellishment (sounds like the kind of
  thing a model would infer rather than find) — it's an exact quote from the post.

## Not independently re-verified

- The Elixir reference implementation's actual file layout, Captain Claw's
  `session_orchestrator.py` internals, and sheath's `spill.py`/`paths.py`/`workspace.py`
  internals were not opened by me (nor, per the report's own sourcing note, by the
  author beyond the README/PyPI-description tables) — both the author and I are reading
  what each project's own documentation claims about its file layout, not the trees
  themselves. The report is explicit about this limitation already; I'm not treating it
  as a new finding, just not re-asserting more confidence than either of us has.
- The recommendation section (hybrid orchestration for Limpet) is a judgment call, not a
  factual claim — nothing to falsify there beyond checking its premises (Limpet's current
  §6 loop shape, obligation ledger, critic.py scope), which are the internal citations
  already verified above.

## Conclusion

CONFIRM. wq#4367's dissection is accurately sourced against all three external projects
and all eight internal repo citations I checked, including exact verbatim quotes and
specific numbers (15K stars, 500% PR increase, 51 tools, 8192-byte spill threshold, 100
MiB spill cap). The one naming looseness ("Bat mode" vs. "Bat") is cosmetic. The author's
own hedges about unconfirmed details are themselves accurate rather than a smokescreen.
No escalation to opus-5@high warranted.
