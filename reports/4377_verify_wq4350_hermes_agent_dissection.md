# wq#4377 — Adversarial verify-pass of wq#4350 (Hermes Agent dissection, PR#2258)

**Verdict: CONFIRM** — the dissection is built from real, correctly-cited source at the
pinned commit, not from search snippets or memory. Every claim I adversarially spot-checked
against the live GitHub repo and the local Limpet/foreman cross-references resolved exactly,
including several verbatim quoted strings and the two "corrections to the ticket's framing"
that are the document's most falsifiable load-bearing claims.

Deliverable: `research/WQ4350_HERMES_AGENT_DISSECTION.md` (merged `6020ad77`, single file,
+398, no code change — this is a research/rca-investigation ticket, so there is no code
probe to run; the applicable probe is re-deriving the citations from primary sources).

## What was shipped

A 399-line dissection of `NousResearch/hermes-agent` pinned at commit
`5fc490e2b34123212d73ef316086f939567a725a`, covering four pillars (skill-creation learning
loop, isolated subagents, curated memory with nudges, FTS5 cross-session recall), a
weaknesses section, a gap-check table against Limpet, and seven Limpet-adoption
recommendations. It opens with two corrections to the ticket's own framing: the "5+ tool
calls" skill trigger no longer exists in code (replaced by a 10-iteration counter), and
session search dropped LLM summarization on 2026-05-18.

## Verification performed

**External repo metadata** — fetched live via `curl https://api.github.com/repos/NousResearch/hermes-agent`
(unauthenticated, per ISSUE-LOG#1466's documented workaround for fleet-search's JSON-API gap):
repo is real, `created_at: 2025-07-22T22:22:28Z` (exact match), `stargazers_count: 254991` /
`forks_count: 55475` vs. the doc's 254,989 / 55,473 — a few hundred stars of organic drift
consistent with the hours elapsed since the doc was written, not a fabricated number.

**Pinned commit** — `GET /repos/.../commits/5fc490e2...` returns that exact sha, dated
2026-10-19T04:16:12Z, consistent with the doc's "main tip at 2026-10-19 04:16Z" claim.

**Source citations, spot-checked at the pinned commit via `raw.githubusercontent.com`**
(six files, both quoted strings and line numbers):

- `agent/turn_iteration_prep.py:165-167` — comment and code match verbatim: "resets whenever
  skill_manage is used" / `agent._iters_since_skill += 1` gated on `"skill_manage" in
  agent.valid_tool_names`.
- `agent/prompt_builder.py:~240-260` — the removed "5+ tool calls" sentence, the billing-shaped
  HTTP 400 on subscription OAuth, and the live replacement text ("When you work out a
  non-trivial workflow, record it with skill_manage for future reuse.") all match exactly.
  This is the doc's central corrective claim (§0.1) and it holds.
- `tools/session_search_tool.py:8` — docstring "No LLM calls — every shape returns actual DB
  messages." matches verbatim; line 23's `_HIDDEN_SESSION_SOURCES` tuple matches the "subagent,
  kanban and tool sessions are hidden" claim.
- `tools/skills_guard.py:27-32` — `INSTALL_POLICY["community"] = ("allow","block","block")` and
  `["agent-created"] = ("allow","allow","ask")` match the doc's claim that a "caution" verdict
  passes for agent-created skills but blocks for community skills, at the cited lines exactly.
- `tools/delegate_tool_toolsets.py:14-23` — `DELEGATE_BLOCKED_TOOLS` frozenset and its five
  inline comments (`delegate_task`/no recursion, `memory`/no shared-MEMORY writes, etc.) match
  the doc's blocked-tool list word for word.
- `agent/curator.py:~444-446` — "If you end the pass with fewer than 10 archives, you stopped
  too early" matches the doc's §5.5 quota criticism verbatim.

**Referenced commit history** — `GET /commits/abf1af54` resolves to
`abf1af540193c30047ff3e7e759c330faf3a880f`, dated 2026-05-18T06:28:45Z, message
"feat(session_search): single-shape tool with discovery, scroll, browse — no LLM (#27590)"
with body text ("~$0.30/call, took ~30s, and laundered FTS5 hits through a model that could
confabulate...") that the doc's §0.2 quotes near-verbatim, including the exact PR number.
This is the doc's second corrective claim and it also holds exactly.

**Local cross-references** — all six files/docs the dissection cites against Limpet exist in
`/home/lumen/foreman` and the cited lines match: `design/wq3806_limpet_architecture.md:251-253`
("The agent cannot reach one workspace's files from another"), `limpet/kb.py:223-224` (FTS5
`kb_entries` schema) and `:279-280` (quote-every-token `_fts_query` sanitizer),
`limpet/workspace_index.py:115` (FTS5 schema), `limpet/injection.py`, plus
`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` and `design/wq3999_rex_skill_dissection.md`.

**PR mechanics** — `git show --stat 6020ad77` confirms a pure docs add, one file, no code
touched, consistent with the ticket's `rca-investigation` archetype; no acceptance probe
applies beyond citation re-derivation, which is what this pass did.

**ISSUE-LOG cross-check** — the doc's stated methodology (fleet-search web/github unusable,
curl to `raw.githubusercontent.com` / `api.github.com` as workaround) matches ISSUE-LOG rows
#1313 (open, same wall) and #1466 (wq#4350's own entry, logged 2026-10-19, workaround
verified), which I independently reproduced this session with the same curl pattern.

## What I tried to break and could not

- Hunted for a padded/rounded star count (common tell for a hallucinated "biggest repo"
  claim) — the actual count differs from the doc by <1000, i.e. real-time drift, not a round
  number.
- Checked whether any cited line number was off by more than editorial slack (a comment line
  vs. the code line next to it) — none were; several were exact to the line.
- Checked whether the "corrected" claims (dropped 5+ trigger, dropped LLM summarization) were
  themselves the fabrication, since a verifier that only checks what the ticket already
  believed would miss a self-serving "correction" — both are backed by a real commit with a
  real PR number and verbatim-quoted commit message body.

No contradiction found. Confirming on the board.
