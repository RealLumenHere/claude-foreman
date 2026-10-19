# wq#4401 — judgment on wq#4386's CONTRADICT of wq#4351 (NanoBot dissection)

**Judge:** claude-opus-5 @ high. **Input:** wq#4386's verify-pass
(`reports/wq4386_verify_wq4351_nanobot_pip_package_mismatch.md`), which
CONTRADICTED wq#4351's `research/WQ4351_NANOBOT_DISSECTION.md` on one claim —
the doc's opening-paragraph `pip install nanobot` installs an unrelated
package — and escalated the question of scope.

## The question, as escalated

> (1) a one-line correction (`pip install nanobot-ai`) as a quick follow-up
> fix, given the rest is otherwise sourced correctly, or (2) is a single
> primary-source-contradicted claim in the opening sentence reason enough to
> re-audit the doc's other unlinked/unquoted factual assertions before calling
> wq#4351 done?

## Decision: (2) — and the re-audit is done, in this ticket

Not because one error mechanically justifies a full re-audit, but because of
**where this specific error came from**. wq#4351's own sourcing note records
that when it was written, the primary search path was entirely unavailable —
"SearXNG's general engines were all suspended/CAPTCHA'd this run … the keyed
DeepSeek-Search fallback carried the whole search" — so a substantial share of
the doc necessarily rests on Chinese tech press, a Mintlify docs mirror, and a
third-party wiki ingest. The `-ai` suffix drop is the signature degradation of
exactly that path.

That makes the pip error a **sampling signal, not an isolated typo**. The
honest test of option (1) is: if I audit the rest of the same-provenance
population, does it come back clean? If yes, ship the one-liner. If no, (1) was
never available. So I ran that audit rather than reasoning about it.

**It came back dirty: two further primary-source contradictions.** Option (1)
would have shipped both.

### Scope I audited, and why it could fail

The population is **every precise identifier or number the doc states without
a primary-source link** — package names, class names, numeric caps, mechanism
claims. That is deliberately *outside* the population wq#4386 checked (it
verified the claims the doc linked or quoted — PR #554's body, `LLMProvider`,
`BaseChannel`, `core_agent_lines.sh` — and those all held). Per wq#2630, a
probe drawn from the population a pass was designed around confirms and never
falsifies; this one could fail, and did, twice in ~8 checks.

Every historical check is pinned to commit **`a1241ee6`** (2026-03-14),
contemporaneous with the doc's sources, not to `main` — both because `main`
has moved a long way since February and because a rev-pinned read is
re-runnable evidence where a live-file read is not.

## Findings

**Confirmed wq#4386's contradiction independently.** `pyproject.toml:2` →
`name = "nanobot-ai"`. `pypi.org/pypi/nanobot-ai/json` → "A lightweight
personal AI assistant framework"; `pypi.org/pypi/nanobot/json` → "Minimalist
robot navigation framework using cascading decision nodes", author "NanoBot
Team". wq#4386's verdict stands on primary source. I also found the *mechanism*
it didn't: `pyproject.toml:117` is `nanobot = "nanobot.cli.entry:main"` — the
console script genuinely is `nanobot`, so any paraphrase that collapses
distribution name into command name produces this exact error.

**New contradiction E2 — the subagent iteration budget is inverted.** The doc:
subagents get 15 iterations "vs. 4 for the dispatching main agent — i.e. the
main loop stays cheap and interactive while delegated work gets more runway."
Primary source at `a1241ee6`: `nanobot/agent/subagent.py:115` →
`max_iterations = 15`; `nanobot/agent/loop.py:56` → `max_iterations: int = 40`.
The main loop's cap is **40, not 4**. Subagents get *less* runway than their
dispatcher, so the architectural reading the doc draws from the numbers is
backwards — this bounds the cost of fan-out rather than extending it. A
dropped zero, same class of transcription failure as the dropped `-ai`.

**New contradiction E3 — `save_memory` is a real tool call, and this one had
propagated into a recommendation.** The doc: `save_memory` is "**not a real
tool call** — handled directly inside the framework rather than round-tripping
through the LLM tool-call protocol, cutting unnecessary I/O … looks like a tool
to the model, costs nothing like a real one to the runtime." Primary source,
`nanobot/agent/memory.py` at `a1241ee6`: `_SAVE_MEMORY_TOOL` (lines 21-45) is a
complete function-tool schema with `history_entry`/`memory_update` parameters;
`MemoryStore.consolidate()` (line 114) passes it as
`provider.chat_with_retry(..., tools=_SAVE_MEMORY_TOOL, tool_choice=forced)`
with `forced = {"type": "function", "function": {"name": "save_memory"}}`
(line 139), with an `tool_choice="auto"` retry for providers that reject forced
choice. It is a real LLM tool call costing an **extra dedicated round-trip** —
the opposite of zero I/O.

This is the most consequential of the three, because **Recommendation B** told
Limpet to adopt the "virtual tool" pattern (exposed to the model, short-
circuited internally) for `evidence.json` writes and `ISSUE-LOG.md` appends on
the strength of it. The pattern as described does not exist in NanoBot. The
narrower true claim — consolidation is a separate pass with a one-tool toolbelt
and a forced `tool_choice`, keeping `save_memory` off the main loop's tool list
— is still a transferable lever, so B is rewritten to that, with its real cost
(an extra round-trip, not a free lunch) stated. Had wq#4351 been closed on a
one-line pip fix, Limpet would have carried a design recommendation sourced
from a misread paraphrase.

### Resolved in the doc's favour

wq#4386's one unresolved flag resolves **for** wq#4351: `SpawnTool` is real —
`class SpawnTool(Tool)` at `nanobot/agent/tools/spawn.py:11`, commit
`a1241ee6`. wq#4386 could not confirm it because it searched current `main`,
where the symbol is `SubagentTool` at `nanobot/agent/tools/subagent.py` and the
old path is gone; it correctly hedged that a rename was possible, and it was.
The doc's "15 iterations" number is likewise primary-source correct — only its
companion number was wrong. Also upheld: the "bypasses LiteLLM" framing was
accurate as of the doc's sources (`litellm>=1.82.1` was a hard dependency and
`nanobot/providers/litellm_provider.py` existed at `a1241ee6`; current `main`
has dropped LiteLLM entirely — flagged as staleness, §3); `@jcheesepkg/nanobot`
(npm, "Lightweight AI assistant - TypeScript port") and `bcefghj/learn-nanobot`
(GitHub, 875 stars) both exist under exactly those names; ClawHub is real
(`nanobot/skills/clawhub/SKILL.md`); Markdown-file memory with no vector DB is
real; "10+ channels" is conservative against 18 channel dirs on `main`.

So the doc's architectural substance — MCP as a ~120-LOC adapter, breadth in
thin adapters behind `LLMProvider`/`BaseChannel`, file-based memory, dynamic
skills — survives intact. The failures are confined to transcribed identifiers
and numbers, which is precisely what the provenance predicted.

## Disposition

- `research/WQ4351_NANOBOT_DISSECTION.md`: all three errors corrected inline
  with pinned primary-source citations, Recommendation B rewritten, an
  **Errata** table added recording what was wrong, what was upheld, and the
  `a1241ee6` pin, plus staleness notes where the doc was right-as-of-sources
  but `main` has moved.
- `kb/ISSUE-LOG.md` row **1474**: the reusable method — treat one
  primary-source contradiction in a doc whose sourcing note admits the primary
  path was down as a sampling signal over the whole unlinked population; and
  pin symbol-existence checks to the contemporaneous commit, since
  current-`main`-only checks return false "inconclusive" on renamed symbols.
  Records that `api.github.com`, `pypi.org/pypi/<pkg>/json` and
  `registry.npmjs.org` all answered unauthenticated from a worker this run, so
  this audit shape needs no token (contrast ISSUE-LOG row 1387).
- **wq#4351 is now dischargeable.** wq#4386's CONTRADICT was correct and
  under-scoped; both are resolved by this pass. No re-dispatch of wq#4351
  needed — the doc is corrected in place.

## Doctrine note

The three errors share one root: wq#4351 asserted paraphrased identifiers as
fact without marking them as single-secondary-sourced. The doc *did* hedge the
numbers it knew were contested (star count, full-repo LOC) and those were the
claims that caused no trouble. The lesson isn't "hedge more" — it's that an
identifier you can check against a primary source in one HTTP call should be
checked, not hedged. All three failures here cost one `curl` each to catch.
