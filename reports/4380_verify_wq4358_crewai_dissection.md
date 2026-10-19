# wq#4380 — Adversarial verify-pass of wq#4358 (CrewAI dissection, PR#2259)

**Verdict: CONFIRM** — every checkable claim in the dissection resolved against a primary
source (vendor docs, vendor `llms-full.txt`, live GitHub source at a pinned tag, a real GitHub
issue, PyPI/third-party structured data), several of them exactly to the word. I went further
than the author's own stated scope: the doc self-discloses that `WebFetch`/git clone of
`crewAIInc/crewAI` was not on the author's allowlist, so its code-level claims (§3's
`BaseAgentTool`/exact-match coworker resolution, §4's manager-tools exception) are sourced
secondhand from a third party's source-reading writeup. I had `fleet-search web` (DeepSeek
Search fallback, same SearXNG-outage workaround the author used) pull the actual
`crewAIInc/crewAI` GitHub source at tag `1.15.16` directly, so this pass independently closes a
gap the author was honest about leaving open.

Deliverable: `research/WQ4358_CREWAI_DISSECTION.md` (merged `2445a441`, single file, +309, no
code change — `rca-investigation`-shaped, no acceptance probe beyond citation re-derivation).

## What was shipped

A 309-line build-vs-buy dissection of CrewAI against Limpet's planned recon→5-stances→reconcile
topology (`design/wq3999_rex_skill_dissection.md`), covering the role/goal/backstory agent
model, the Crew/Flow duality, the DelegateWork/AskQuestion delegation primitives, sequential vs.
hierarchical process modes, the 75-tool catalog, the new unified-memory composite-scoring
system, licensing/dependency posture (including a live LiteLLM supply-chain-incident caveat),
and a final recommendation: CrewAI's `Flow`+`Crew` is the fastest structural match if schedule
pressure wins, but only its vocabulary (not its runtime or its delegation permission model)
should be adopted if dependency-surface minimization wins.

## Verification performed

**PR mechanics** — `git show --stat 2445a441` (the commit on `main`; `a60c874e` is the
author-branch duplicate, same content, not the merged sha) confirms a pure docs add, one file,
+309/-0, consistent with the `rca-investigation` archetype.

**LiteLLM PyPI quarantine (§8) — the single most extraordinary, most falsifiable claim in the
doc.** `fleet-search web "litellm pypi quarantined security incident"` returned liteLLM's own
blog, `docs.litellm.ai/blog/security-update-march-2026`, "Security Update: Suspected Supply
Chain Incident," dated March 24 2026, with compromised version IDs `1.82.7`/`1.82.8` — this is a
real, dated incident, not a hallucination built from a plausible-sounding premise. A second
query, `"CrewAI litellm-removal-guide native integrations"`, returned
`docs.crewai.com/v1.15.20/en/learn/litellm-removal-guide` with the text *"The litellm package
was quarantined on PyPI due to a security/reliability incident. If you rely on LiteLLM-dependent
providers, you should migrate to native integrations"* — a **verbatim match** to the doc's
quoted sentence.

**Memory composite-scoring defaults (§7).** The doc hedges this one itself ("the English docs
state the formula but not every default inline," crediting a Chinese-language blog for the
numbers instead). I found the defaults directly on a different vendor-doc version:
`docs.crewai.com/v1.14.7/en/concepts/memory` ships an explicit parameter table —
`recency_weight=0.3`, `semantic_weight=0.5`, `importance_weight=0.2`,
`recency_half_life_days=30` — an **exact match** to both the dissection and the original
ticket's framing ("30pct recency 30-day half-life, 50pct semantic, 20pct importance"). This is a
stronger verification than the author had, not just a repeat of it.

**DelegateWork/AskQuestion mechanics (§3) — the code-level claim the author flagged as
unverified.** `fleet-search web` on `BaseAgentTool coworker exact match normalized role` pulled
`github.com/crewAIInc/crewAI/blob/1.15.16/lib/crewai/src/crewai/tools/agent_tools/base_agent_tools.py`
directly. The live `sanitize_agent_name` docstring/body: *"Sanitize agent role name by
normalizing whitespace and setting to lowercase... Converts all whitespace (including newlines)
to single spaces and removes quotes,"* with the actual code
`normalized = " ".join(name.split()); return normalized.replace('"', "").casefold()` — matches
the doc's "exact-match on the normalized role string (whitespace collapsed, quotes stripped,
casefolded)" exactly, down to the operation order. `_execute`'s own docstring, pulled in the same
fetch, reads "case-insensitive and whitespace-tolerant matching" — consistent with the doc's "does
not guess from meaning or infer a best match," no fuzzy/semantic fallback found in the source.

**"Manager agent should not have tools" exception (§1, §4).** `fleet-search web` found
`github.com/crewAIInc/crewAI/issues/1851`, "[BUG] 'Exception: Manager agent should not have
tools'... when used in a flow and hierarchical crew" — confirms the exception string and the
enforcement the doc describes are real, not invented.

**GitHub issue #448 (§3).** `fleet-search web` returned the actual issue, "Questions about agent
allow_delegation #448," containing the tool-prompt text *"they know nothing about the task, so
share absolute[ly all necessary context]"* — matches the doc's quoted framing of the delegation
tool's own prompt almost verbatim, and confirms the "delegating back and forth" failure-mode
framing is grounded in a real user report, not a hypothetical the author invented to sound
authoritative.

**`Process.hierarchical` requiring `manager_llm`/`manager_agent` (§4).** `fleet-search web`
pulled CrewAI's own `docs.crewai.com/llms-full.txt`: *"`process=Process.hierarchical` requires
either `manager_llm=` or `manager_agent=`. Without one, kickoff raises at validation time"* —
matches the doc's claim exactly, from the vendor's own consolidated reference doc rather than a
third party.

**75 tools / MIT / no-LangChain / 141k LOC (§6, §8, §9).** `fleet-search web` pulled
`raw.githubusercontent.com/EdwardAThomson/agent-reviews/.../data/agents/crewai.yml`: `license:
MIT`, `loc: 141200`, "75 pre-built tools spanning web search, scraping, DBs, cloud, automation,"
"no LangChain dependency" — confirms the third-party review repo is real (not a fabricated
citation) and its numbers match the dissection's §6/§9 claims exactly, including the specific
141k figure the doc uses to puncture the "lean" marketing framing.

**Local cross-references.** `design/wq3999_rex_skill_dissection.md:131-182` — the
recon/5-stance/reconcile topology, "forbidden from concluding" (`re-recon.md:16-18`), and
stance non-communication (`re-safety.md:12,28`) all read exactly as the dissection describes them.
`limpet/critic.py:12-35` — the propose→critic→adjudicate module header matches the "deterministic
structural gate runs first" claim the dissection leans on in §9's adjudication-gap argument.
`limpet/model_router.py` and `design/wq3806_limpet_architecture.md` exist and are consistent with
the "agent core is still design-only" framing used throughout.

## What I tried to break and could not

- **Treated the LiteLLM-quarantine claim as the doc's weakest point on first read** — a security
  incident on a widely-used package, cited only by the vendor's own migration guide, is exactly
  the shape of thing a model could confabulate to sound current. It independently resolved to a
  real, dated liteLLM security-blog post with real compromised version numbers, plus a verbatim
  matching quote from CrewAI's own docs. Not fabricated.
- **Pushed on the one spot the author admitted was unverified** (BaseAgentTool/coworker-matching
  code, sourced from a third party's reading, not the file itself) rather than taking the doc's
  own hedge as cover. Found the actual GitHub source at a pinned tag; it matches to the operation
  order.
- **Checked whether the third-party review repo (`EdwardAThomson/agent-reviews`) was itself
  invented** — it resolves to a real GitHub repo with a real commit hash and structured YAML data
  that independently corroborates (not just repeats) the PyPI/vendor-doc numbers.
- **Checked the one internally-hedged number (composite-score defaults)** against a different
  vendor-doc source than the author used — found a cleaner, more direct confirmation than the
  author had.

No contradiction found. Confirming on the board.
