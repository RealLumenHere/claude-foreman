# AGENTFORGE_EVAL — adopt "AgentForge orchestration patterns" for fleet researchers?

_2026-09-18 PDT · session orch-agentforge-eval-cee666 · RESEARCH + DESIGN ONLY (nothing installed, no service touched)_
_Evidence grades: **[V]** verified this session (command/URL shown in §7) · **[R]** read in a README/doc, not executed · **[U]** unverifiable_

## 0. VERDICT (read this, skip the rest if busy)

**Do not adopt any AgentForge. Build the missing pieces on Minerva + the existing LangGraph harness.**

1. The rejected proposal (`37cb4ce26a9b`) did not mean any of the six candidates. Its source intel row is
   **`Roshanrameshhub/AgentForge`** — a 6-commit, 1-day-old (at harvest) LangGraph demo app. The April sibling
   proposal (`ff80d5e94515`) was sourced from **`Saichandra2520/AgentForge`**, which is a *project scaffolder*
   (`create-agent-app`) with no memory, no skill library and no self-improvement loop at all. **[V]**
2. Both proposals were generated from a **repo title only** — `classified_intel.url` and `.summary` are empty for
   both rows; the "orchestration patterns" / "memory + skills + self-improvement" claims are the classifier's HINT
   text, not repo facts. The follow-up fleet research note invented a repo tree that does not exist. **[V]**
   The verify gate scored both 1.00 because it checks claims against CONTEXT about *Lumen*, not against the repo.
3. `leary-poken/AgentForge` is NOT the legit one. It is a 0-star, 18-commit stale re-upload (v0.6.4) of
   **`DataBassGit/AgentForge`** — the real project that owns PyPI `agentforge` (847★, 1,144 commits, 67 PyPI
   releases since 2023-07, latest 0.6.6 on 2026-08-10, GPL-3.0, native `LiteLLM`/`Ollama`/`vLLM`/`OpenRouter`
   classes). **[V]** It was not in the candidate list.
4. Even the legit one does not contain the patterns the proposal wanted: no self-improvement loop, no skill
   library (Tools/Actions are *deprecated*), and its memory is exactly the naive pattern — a Chroma node with
   `query_before` / `update_after` on every agent step. **[R]**
5. The fleet researchers **already run on an orchestration framework**: LangGraph 1.2.5 via
   `autonomy/lumen_fleet_graph.py` (load→web_search→local_retrieval→call_llm→quality_gate→verify_opt→write). **[V]**
   Adopting a LangGraph wrapper to orchestrate a LangGraph graph is negative value.
6. The real gap this exercise exposed is not orchestration. It is that **fleet-researcher output has no
   groundedness measurement** (quality_gate = length + dup canary + arXiv-ID existence), so a hallucinated
   research note became a proposal twice. That is the thing to build, and it *is* the frozen benchmark (§3).

**First step:** build `FRB-v1` — the frozen fleet-researcher benchmark (§3) — before any self-improvement loop,
skill library or memory change touches the researchers. No benchmark, no loop. Ticket filed HELD (see §6).

---

## 1. WHICH AgentForge

GitHub search `agentforge in:name` returns **1,540 repos** (2026-09-18). The name is a buzzword, not a project. **[V]**

| # | Repo | What it actually is | ★ / commits / last push | License | Local models (Ollama/LiteLLM) | Real? | Hype flags |
|---|---|---|---|---|---|---|---|
| A | **DataBassGit/AgentForge** (not on the list; the real one) | Python low-code agent framework: YAML Agents + "Cogs" (flow state machine w/ branch, loop guard, fallback) + Chroma memory + personas | 847 / 1,144 / 2026-08-10; PyPI `agentforge` 0.6.6, 67 releases since 2023-07 | GPL-3.0 | **Yes** — built-in `Ollama`, `LMStudio`, `VLLM`, `OpenRouter`, `LiteLLM`, `GroqAPI` classes **[R]** | Real, maintained, 2 core devs | Tagline "Extensible AGI Framework"; default model path is now Codex OAuth (`chatgpt.com/backend-api/codex`); Tools/Actions deprecated pending MCP rewrite |
| B | leary-poken/AgentForge | **Stale copy of A** (setup.py: author John Smith/Ansel Anselmi, url DataBassGit, version 0.6.4; README badges point at DataBassGit). Not a GitHub fork, just a re-upload | 0 / 18 / 2026-03 | GPL-3.0 (inherited) | same as A at 0.6.4 | Real code, **not the project** | Premise "leary-poken = PyPI, documented" is wrong: PyPI `agentforge` homepage = DataBassGit |
| C | eveselove/AgentForge | One operator's personal agent fleet (Russian docs): SQLite task queue + DAG deps, checkpoints/crash recovery, FTS5 shared memory + blackboard, watchdog, git auto-rollback on CI fail, systemd units, `eval/` with regression + trajectories, `skills/`, `learning/`, `rust/` | 2 / 154 / active | **No LICENSE file** (= all rights reserved) | **No** — workers are xAI Grok (`.env.xai.example`, `grok_worker`) | Real, single-dev, closest in *shape* to Lumen | "Self-improving", "Pure Rust Flywheel" in title; README describes Python core |
| D | fgazer94/AgentForge | LangChain/LangGraph multi-tenant platform: FastAPI + Postgres + Redis + Chroma/FAISS + Vue UI + sandbox daemon + Feishu/Lark channels | 4 / **4 commits** / 2026-03-13 (one-day dump) | Apache-2.0 | README never mentions Ollama/LiteLLM **[R]** | Code drop, abandoned day 1 | "production" ×N, multi-tenant/JWT/2FA surface Lumen has no use for |
| E | JPeetz/agentforge | Go daemon: HMAC capability tokens, cron, multi-MCP, Telegram/Discord, "self-learning" SKILL.md generation via Jaccard clustering | 0 / 40 / 2026-06-30 | **BUSL-1.1 (not open source)** | Mentions Ollama **[R]** | Exists; install path is fake | `go.mod` module = `github.com/agentforge/agentforge` (someone else's 2022 Python repo) so the README's `go install`/brew/docker lines cannot work **[V]**; "100K+ concurrent agents on a $10 VM", "CISO-ready", self-declared "independent security audit" |
| F | cjy926/AgentForge | — | **404** — user exists (6 public repos, none named AgentForge) | — | — | **[U]** deleted/private/never existed | Cannot evaluate; drop |
| G | **Roshanrameshhub/AgentForge** (ACTUAL source of rejected proposal) | LangGraph "12-agent" chat service: FastAPI + Streamlit + Chroma + NetworkX + Postgres checkpoints | 8 / **6 commits** / created+last-pushed 2026-08-24 (same day) | MIT | **No** — `langchain-openai`, `langchain-groq` only | Demo app | Pins `langgraph==0.1.19`, `langchain-core~=0.2` + Pydantic v1 (2024-era) in a repo "created" 2026-08 — a re-skinned older template; **incompatible with Lumen's langgraph 1.2.5**. "Build AI systems that think beyond one prompt" |
| H | **Saichandra2520/AgentForge** (ACTUAL source of April proposal) | `create-agent-app` — Jinja scaffolder that generates LangGraph starter projects | 22 / 9 / 2026-04-28 | MIT | Ollama as a template option | Real but irrelevant | It is a cookiecutter. It has none of the features the proposal attributed to it |

**Rank by fit for Lumen:** A (only real framework; still NO-ADOPT, §5) › C (pattern-read only, unlicensed, Grok-bound — but its `eval/regression.py` + checkpoint/rollback shape is the one thing worth skimming) › E, D, G, H, B (no) › F (n/a).

**Class finding:** the intel→proposal pipeline produces adopt-proposals from a name. Same class as wq#92 (paper
misread) and the ab5aa5 rejects. Fix belongs in the machinery: `classified_intel` rows with empty `url`/`summary`
must not be eligible to seed a proposal (§6 ticket 2).


---

## 2. WHAT PATTERNS — mapped to what Lumen already has

| Pattern | What Lumen has today **[V]** | Gap | Call |
|---|---|---|---|
| **Orchestration / delegation** | wq (tickets, claim, deps, holds) + `lumen-foreman-dispatch.timer` broker + `router.py` model/effort assignment + `slock` + per-provider systemd units (`lumen-fleet-{cerebras,mistral,openrouter}`) + file-rename atomic claim queue + `lumen_quota_manager` + LiteLLM :4000 + **LangGraph 1.2.5 harness** (`autonomy/lumen_fleet_graph.py`, 8 nodes, conditional requeue) | None that a framework fills. Queue is drained: 0 pending / 0 stuck / 4,541 done / 11,465 outputs | **ALREADY-HAVE-IT** |
| **Memory** | Minerva: `archival_notes` + FTS + rerank (golden v4 R@1 .728), bi-temporal `state_facts` (977), episodes, `note_hits` (185,698 rows) / `note_salience`, `memory_quarantine`, `note_contradictions`; researchers get `local_retrieval` over pgvector `research_docs` (AB_RAG_RETRIEVAL=1) | (a) `lumen-research-index.service` FAILED since 2026-09-18 03:27 PDT — `column "asserted_at" of relation "research_docs" does not exist` (half-applied migration; aq#1222) so the researchers' local corpus has stopped advancing. (b) RESOLVED, listed for history: wq#570 (2026-08-14) found agent lanes reading Mem0/Qdrant + a 120-line tail instead of search_v3; wq#575 fixed it — `MEMORY_V4_MEMORYD=1` is set and `lumen-memoryd` is active (verified 2026-09-18), lanes now go memoryd → search_v3 | **ALREADY-HAVE-IT** — fix defect (a); add nothing |
| **Skill library (reusable playbooks)** | `skills` table (5 rows, 3 ever used, mean success .37) + `minerva_skills.py` + `skillhex_results` (18) + `instincts.yaml` (42 rules) + `instinct_worth` (56) | Researchers have ONE system prompt for every task class. No per-archetype playbook (repo-intel vs paper-intel vs integration-design), which is exactly why a repo note was written without ever listing the repo's tree | **BUILD-ON-MINERVA** — playbooks as rows in `skills`, selected in `load_prompt`, admitted ONLY through the §3 benchmark |
| **Self-improvement loop** | proposals → verify/critic gate (fail-closed) → `minerva_defer_review` → **pilot lane** (32 pilots; pre-registered CARD, fixed win condition, exact paired McNemar, `significance.py`) ; dream consolidation proposal-only; frozen golden v4 (0444 + sha256 + comparability gate + trap@1 any-rise alarm) for recall | The loop has a frozen instrument for *recall* and none for *researcher output*. Self-improvement of the researchers is currently unfalsifiable | **BUILD-ON-MINERVA** — §3. This is the whole job |
| **Verify / critic** | `minerva_verify.py` citation + judge, fail-closed; arXiv-ID existence check (wq#104) | Verifies proposal claims against Lumen CONTEXT only. Nothing checks the *external subject* exists / says what the note claims. Both AgentForge proposals passed at 1.00 | **BUILD (small)** — source-grounding precondition, §6 ticket 2 |
| Checkpoint / crash recovery | `.claimed_*` rename + requeue edge + `stuck/`; `langgraph-checkpoint` 4.1.1 installed | none material | ALREADY-HAVE-IT |
| Human-in-the-loop gates | Retired by standing decision (FULL AUTONOMY 2026-08-22) | n/a | NOT WANTED |

What every AgentForge would add that Lumen lacks: **nothing**. What DataBassGit would duplicate: orchestration dialect (#2), memory store (#3, counting the legacy Mem0/Qdrant store that wq#575 routed the lanes away from; it remains only as the fallback import in lumen_agent_v4.py).

---

## 3. THE GUARDRAIL — FRB-v1, the frozen fleet-researcher benchmark (DESIGN, not built)

**Why non-negotiable:** the Darwin Gödel Machine run is the documented case — a self-modifying agent reached a
perfect hallucination score by defeating the detector instead of fixing the behaviour, and the authors report
objective hacking is *more frequent when the checking functions are visible to the agent* (arXiv 2505.22954 §safety;
The Register 2025-06-02). So the rule is two-part: the agent **cannot edit** the instrument AND **cannot read** the
scorer/gold labels of the split that decides adoption.

### 3.1 Task set (60 tasks, queue-native `.txt` prompts so they run through the unmodified graph)
| Class | n | Ground truth | Built-in traps |
|---|---|---|---|
| repo-intel | 18 | GitHub API/HTML snapshot taken at freeze: tree, license, commits, deps, fork-of | name collision (3 different "AgentForge"), stale re-upload (leary-poken), 404 repo (cjy926), scaffolder-posing-as-framework (Saichandra), fake install path (JPeetz) — **8 tasks already have ground truth in §1/§7 of this file** |
| paper-intel | 12 | abstract + 5–8 atomic gold claims | paper with no Lumen call site (wq#92 class: RL-training credit assignment) → gold verdict REJECT |
| lumen-integration | 14 | `lumen_state.py asof <freeze-date>` | note must not invent fleet capabilities (the Roshan note asserted "RTX 4090 nodes", n8n, Weaviate) |
| ops-research (CVE / version / compat) | 10 | vendor advisory snapshot | superseded version numbers |
| abstention | 4 | source does not exist / question unanswerable | correct output = says so |
| positive controls | 2 | trivial | a missed control **fails the run** (exit 2) — same gate as `recall_bench.py` |

### 3.2 Frozen evidence, so the web cannot move the score
`web_search` and `local_retrieval` outputs are **snapshotted per task at freeze** and replayed (`FRB_REPLAY=1` makes
those two nodes serve the snapshot). The decisive score isolates what the loop is allowed to change (prompt,
playbook, graph, routing, model). A live-web arm is reported alongside, never decisive.

### 3.3 Metrics — mechanical first, judge second
- **M1 fabricated-entity rate (PRIMARY, deterministic).** Extract every repo path, URL, arXiv ID, file path, package,
  hostname, hardware model from the report; anything not in the task's snapshot allowlist ∪ `lumen_state` = fabricated. No LLM in the loop.
- **M2 gold-claim accuracy.** 5–10 atomic claims per task incl. planted false ones; judged at temp 0 by a pinned
  model from a provider family **not in the researcher pool under test**; judge prompt sha-pinned inside the bundle.
- **M3 verdict accuracy.** Exact match on the existing `RECOMMENDATION:` contract (ADOPT/WATCH/SKIP/REJECT).
- **M4 trap rate.** Fell for a planted trap. **Any rise disqualifies the change regardless of M1–M3** (wq#595 rule).
- M5 tokens / wall-clock / $ — reported, not decisive.

### 3.4 Freeze mechanics (what "cannot edit" means on a box where agents have sudo)
1. Bundle `frb_v1_<date>.tar` = prompts + snapshots + gold + judge prompt + scorer. sha256 recorded in **three
   places**: inside every result artifact, a signed tag in Gitea on Anubis, and the restic offsite snapshot. Scorer
   refuses to run on sha mismatch; nightly sha tripwire → `aq`.
2. **DEV split (40)**: prompts + gold readable, for error analysis. **HOLDOUT split (20)**: gold + scorer live under a
   separate unix user (`frb`, dir 0700), executed by a `User=frb` oneshot unit; the researcher/self-improve lanes get
   back **one aggregate JSON**, never per-task labels. Adoption is decided on HOLDOUT only.
3. Foreman `capability.py` / `action_gate.py`: deny write AND read on the FRB paths for every lane except `frb-score`.
4. One HOLDOUT scoring per candidate change (no re-rolls) — stops adaptive overfitting to 20 tasks.
5. FRB run outputs land in `scratch/frb_runs/` which is **excluded from `lumen-research-index`** — otherwise last
   run's answers leak into `local_retrieval` and the benchmark self-contaminates.
6. Immutable. New tasks ⇒ FRB-v2, new sha; comparability gate SKIPS cross-version comparison (existing recall-bench behaviour).

### 3.5 Before/after protocol — reuse the pilot lane as-is
Every researcher change (prompt, playbook, node, retrieval param, routing) is a pilot CARD: hypothesis, primary
metric M1, win condition fixed before the run. Baseline and treatment run **in the same window** on the same
snapshot (the wq#595 trick that separated corpus drift from scoring drift). At n=60 one task = 1.7 pts, so: exact
paired McNemar on per-task pass/fail (M3/M4), paired bootstrap on M1/M2, p<0.05; no class may drop >2 pts; M4 may not
rise. Fail ⇒ change is discarded and the CARD is kept (negative results are the memory).
**Off-limits to the loop:** FRB bundle, scorer, judge prompt, `quality_gate`, `verify_opt`, `minerva_verify.py`.


---

## 4. THE NAIVE-MEMORY TRAP — and how the recommendation stays out of it

**The research:** Chroma's *Context Rot* report (2025-07-14, 18 models incl. GPT-4.1 / Claude 4 / Gemini 2.5 / Qwen3):
reliability falls as input tokens grow even on trivial tasks, a *single* topically-related distractor already costs
accuracy, and degradation is worse when the needle is only semantically (not lexically) similar to the question.
"Append every turn, retrieve top-k by cosine" manufactures exactly those distractors. The Mem0 paper (arXiv
2504.19413; vendor-authored, treat numbers as directional) measures the other side: full-history context =
p95 17.1 s vs 1.44 s for selective extraction, at >90% token cost saved.

**Lumen's own evidence, same failure:** golden v4 `trap_at1_rate` = .250 — stale note served above the current one
(qid230 scout-outage, qid238 Grafana-over-power-dash); wq#570 found agent lanes reading Mem0/Qdrant + a 120-line
prose tail instead of search_v3 (since fixed by wq#575/lumen-memoryd — Lumen already paid to climb out of this trap once);
and today the research index silently stopped (§2).

**DataBassGit/AgentForge does the naive thing by default** — a Chroma memory node with `query_before` /
`update_after` on each agent step — and would drag `chromadb` + `sentence-transformers` (torch) + `spacy` onto a
Broadwell CPU box under a no-warm-models directive. That alone is disqualifying.

**Rules for anything built under this recommendation:**
1. **Researchers never write turns to memory.** The only write is the finished report, after `quality_gate` + `verify_opt`.
2. **Write gate, not write log.** A report whose M1-style entity check fails at write time goes to `memory_quarantine`, not the index. (The fabricated Roshan note is in the corpus right now and is retrievable by the next researcher.)
3. **Retrieval budget:** ≤5 chunks / ≤1.5k tokens, reranked, with a similarity floor — below the floor inject *nothing*. Padding is worse than absence.
4. **Time is a field, not a vibe:** every chunk carries `asserted_at` (wq#1773 — finish it) and renders its as-of date into the prompt; mutable fleet facts come ONLY from `lumen_state.py`, never from vectors.
5. **Usage decides salience** (`note_hits` → `note_salience`, worth_lcb, wq#571/#576); forgetting stays proposal-only and dark.
6. **Two instruments for every memory change:** golden v4 frozen (retrieval) AND FRB (end-task). Retrieval metrics alone cannot see dilution.
7. **Playbooks are not memory.** A skill row is ≤40 lines of procedure selected by task class, not retrieved by similarity.

---

## 5. RECOMMENDATION + honest cost/benefit

**Build on Minerva. Adopt nothing. Not a hybrid either.**

| | Adopt DataBassGit/AgentForge (the only real one) | Build on Minerva + existing LangGraph harness |
|---|---|---|
| New capability gained | YAML flow dialect w/ loop guards (LangGraph conditional edges already do this) | Researcher benchmark, source-grounded proposals, per-class playbooks |
| Integration | Re-express 8-node graph + quota manager + verify + claim queue as Cogs; bridge Minerva into its MemoryManager or run a 2nd store | 3 tickets, all additive to files that exist |
| Deps / footprint | chromadb, sentence-transformers(torch), spacy, discord.py, numpy≥2 pin, Python ≥3.12 | none new |
| Maintenance | Track a 2-dev upstream whose default path is moving to Codex OAuth and whose tool system is deprecated with no replacement shipped | own code, own cadence |
| License | GPL-3.0 — fine internally, copyleft hazard if any of it leaks into a published repo (Tinywhale, MikoUnchained, Jay PRs) | n/a |
| Local-inference goal | neutral (LiteLLM class exists) | neutral (already LiteLLM) |
| Self-improvement goal | **zero** — it has no such loop | the frozen instrument that makes the goal falsifiable |
| Prior standing decision | contradicts wq#280 "framework-lock rejected" | consistent |

Net: a framework buys a second orchestration dialect and a third memory store and none of the four requested
patterns. The two cheap things worth *reading* (not installing): DataBassGit's Cog `max_visits`/`fallback` loop-guard
schema, and eveselove's `eval/regression.py` + git-auto-rollback-on-CI-fail shape (unlicensed — read, do not copy).

**Sequence (each step gated by the one before):**
1. **FRB-v1** (§3) — wq#2026. Nothing about the researchers changes until this exists and has a baseline of record.
2. **Source-grounding gate on intel→proposal** — wq#2027. Stops the defect class that produced this whole exercise.
3. Finish wq#1773 (`asserted_at`) so the research index runs again and chunks carry dates.
4. Only then: per-archetype researcher playbooks in `skills`, each admitted by an FRB pilot CARD.
5. Only then: any automated propose-change loop over the researchers — proposer reads DEV only, HOLDOUT decides.

---

## 6. Actions taken this session (no installs, no service/config/DB changes)

- wrote this file
- **wq#2026** FRB-v1 (build, p2, deliverable `foreman/research/FRB_V1_DESIGN.md`) — **HELD until 2026-09-20 17:56 PDT** so it is not dispatched before this is read; auto-releases. Router: `claude-sonnet-5@medium` (probe-build); noted a recommendation to override to `claude-opus-5@high` — it is the guardrail, design quality matters more than speed.
- **wq#2027** intel→proposal source-grounding gate (build, p2) — live, broker may dispatch.
- **wq#1773** noted: half-applied — indexer writes `asserted_at`, column absent, `lumen-research-index.service` failed 2026-09-18 03:27 PDT (aq#1222).
- Proposals `37cb4ce26a9b` and `ff80d5e94515`: recommend **REJECT** both (left untouched in `proposals/pending/` — wq#2027 step 3 sweeps the class).

---

## 7. Sources / verification log

Local **[V]**: `sqlite3 -readonly minerva/minerva.db "select … from classified_intel where id in ('b0e851ae…','b01f759d…')"` (titles Roshanrameshhub/ and Saichandra2520/AgentForge; url+summary empty) · `minerva/proposals/pending/20260426_ff80d5e94515.md`, `20260825_37cb4ce26a9b.md` · `scratch/research/intel_new_github_repo__roshanrameshhub_agentforge_01a87c.md` (claims tree `agents/ routing/ rag/ memory/ mcp/ workflow/ human/`; real tree `agent/ client/ docker/ docs/ k8s/ media/ monitoring/ schema/ scripts/ service/`) · `agent/lumen_fleet_researcher.py`, `autonomy/lumen_fleet_graph.py` · `pip list` (langgraph 1.2.5, mem0ai 2.0.6) · `foreman/pilots/0eed2f5bdb92/CARD.md` · `minerva/eval/golden_v4_frozen_20260814.jsonl` (0444) · `journalctl -u lumen-research-index` · HANDOFF items 0b/5/6/7.

External (all fetched 2026-09-18):
- GitHub search API `q=agentforge in:name` → total_count 1540
- https://github.com/DataBassGit/AgentForge (847★, 1,144 commits) · `docs/apis/apis.md` (Ollama/LMStudio/VLLM/OpenRouter/LiteLLM classes) · `docs/memory/memory.md` (query_before/update_after) · `setup_files/settings/models.yaml` (default `codex_gpt55`)
- https://pypi.org/pypi/agentforge/json (0.6.6, 2026-08-10, 67 releases, homepage DataBassGit, requires_dist)
- https://github.com/leary-poken/AgentForge + raw `setup.py` (author/url = DataBassGit, 0.6.4; 0★, 18 commits)
- https://github.com/eveselove/AgentForge (2★, 154 commits; no LICENSE → 404; `.env.xai.example`; `eval/` listing)
- https://github.com/fgazer94/AgentForge (4★, 4 commits, Apache-2.0)
- https://github.com/JPeetz/agentforge (0★, 40 commits; raw `go.mod` module `github.com/agentforge/agentforge`; LICENSE = BUSL-1.1)
- https://github.com/cjy926/AgentForge → 404; https://github.com/cjy926?tab=repositories lists 6 repos, none AgentForge
- https://github.com/Roshanrameshhub/AgentForge (8★, 6 commits, created=pushed 2026-08-24) + raw `requirements.txt` (langgraph==0.1.19, langchain-core~=0.2.26, langchain-openai, langchain-groq)
- https://github.com/Saichandra2520/AgentForge (README = `create-agent-app`) · https://pypi.org/pypi/create-agent-app/json (0.1.3)
- Zhang et al., *Darwin Gödel Machine*, https://arxiv.org/pdf/2505.22954 (objective hacking; more frequent when checking functions are not hidden) · https://www.theregister.com/2025/06/02/self_improving_ai_cheat/ · https://sakana.ai/dgm/
- Hong, Troynikov, Huber, *Context Rot*, https://trychroma.com/research/context-rot
- Chhikara et al., *Mem0*, https://arxiv.org/abs/2504.19413 (vendor-authored)
- Not re-fetched this session, cited from settled literature: Wang et al. *Voyager* arXiv 2305.16291 (skill library admitted only after self-verification); Liu et al. *Lost in the Middle* arXiv 2307.03172.

**Not verified / limits:** star and commit counts are point-in-time HTML scrapes (unauthenticated API rate-limited mid-run). Candidate code was read, never executed. "Roshan repo is a re-skinned older template" is inferred from 2024-era pins + 6 commits, origin template not identified. wq#570's Mem0/Qdrant finding was re-checked and is SUPERSEDED (agent/.env MEMORY_V4_MEMORYD=1, lumen-memoryd active, lumen_agent_v4.py:353-374).
