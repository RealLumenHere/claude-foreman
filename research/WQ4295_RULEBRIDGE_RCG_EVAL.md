# wq#4295 — RESEARCH: RuleBridge + Rule Coherence Graph — contradiction detection for fleet rules

**VERDICT: DO NOT ADOPT either RuleBridge (`github.com/Shadorux/RuleBridge`, npm
`@shadorux/rulebridge` 0.1.0) or Rule Coherence Graph (`github.com/alast9/rule-coherence-graph`,
PyPI `rule-coherence-graph` 0.7.0). Built and ran a minimal offline linter instead
(`research/rule_lint.py`) against the fleet's actual rule corpus, `/home/lumen/instincts.yaml`
(477 lines, 35 rules, schema 2 — not the hypothetical "245-line ORCHESTRATOR-HANDOFF.md" the
ticket named; no file by that name exists anywhere in the worktree, foreman, kb, bin, or
agents/work-queue trees — `grep -rl "ORCHESTRATOR-HANDOFF"` over all four came up empty). The
real result argues for the lightest version of "simple linter," not even the one this ticket
sketched: a handful of structural checks, no keyword/polarity heuristic, no graph DB, no LLM
judge.**

## 1. What the two tools actually are (fleet-search web, DeepSeek fallback — SearXNG engines
were all suspended/CAPTCHA'd this session; `fleet-search github` 401'd, no usable token)

**RuleBridge** (`npmjs.com/package/@shadorux/rulebridge`, v0.1.0): an ESLint-shaped CLI for
multi-file AI-agent instruction sets. Its worked example is three *different tools'* config
files drifting — `.cursor/rules/typescript.mdc`, `.github/copilot-instructions.md`, `AGENTS.md`,
`CLAUDE.md` each telling the agent to use a different package manager. It reconciles across
Codex/`AGENTS.md`, Claude Code/`CLAUDE.md`, Cursor/`.cursorrules`+`.cursor/rules/**/*.mdc`, and
GitHub Copilot/`.github/copilot-instructions.md` — a fixed list of file formats it knows how to
parse and write back into (`rulebridge import`, `rulebridge fix --dry-run`). Lumen uses none of
Cursor, Copilot, or Codex `AGENTS.md`; its rule corpus is one hand-authored YAML file with its
own schema. RuleBridge has nothing to parse here — adopting it would mean writing a new
format adapter for a tool whose entire value-add *is* its adapters, i.e. paying the integration
cost without the drop-in benefit.

**Rule Coherence Graph** (`pypi.org/project/rule-coherence-graph`, v0.7.0): loads a rule corpus
into a typed Neo4j graph (`Rule`/`RuleFile`/`CONFLICTS_WITH` edges, `docker compose up -d neo4j`,
bolt://localhost:7687) and runs conflict detection in two passes — syntactic, and semantic
(hashing-embedding by default, real sentence-transformers or an LLM judge as paid upgrades).
RCG ships its own benchmark (`benchmarks/RESULTS.md`, 62 labeled rule pairs, 26 conflict / 36
ok):

| Config | Precision | Recall | F1 |
|---|---|---|---|
| syntactic only | 1.000 | 0.500 | 0.667 |
| + semantic (hashing) + MockJudge | 0.867 | 0.500 | 0.634 |
| + semantic (sentence-transformers) + MockJudge | 0.619 | 0.500 | 0.553 |

RCG's own numbers say the cheap pass caps at recall 0.5, and the expensive pass (real
embeddings, which needs `pip install 'rule-coherence-graph[embeddings]'`, plus `--judge
anthropic` to turn recall into true positives on keyword-disjoint conflicts) is what it costs to
go higher — a reasoning call per candidate pair, every run, against a 35-rule, single-author
corpus. Not worth it before the cheap pass has even been tried here.

## 2. What the fleet already has that neither tool knows about

`/home/lumen/instincts.yaml` already carries its own supersession and lineage mechanism — a
`supersedes:` field per rule (e.g. `thor-only-hard-rule` supersedes `reboot-gate`,
`fabricated-evidence` supersedes `self-generated-evidence`), exercised live: the file's own
footer documents `reboot-gate` and `self-generated-evidence` as deliberately and fully removed
at the schema-2 restructure (wq#959/#960/#961), with the *reason* kept in a footer comment
rather than a tombstone rule. Changes to it are proposal-only — `consolidation-proposal-only`
(tier 0, `/home/lumen/instincts.yaml:78-84`) routes all candidates through
`minerva/dream/instincts_candidates.yaml` for Frank review; nothing auto-edits it. That is
already more governance than either GitHub tool provides out of the box.

## 3. Ran it for real against the actual corpus, not a synthetic one

`research/rule_lint.py` implements three checks (`check()`, `research/rule_lint.py:72-110`),
run with `--selfcheck` (clean/dirty fixtures, confirms the checks can both pass and fail —
`research/rule_lint.py:141-155`) and then for real against `/home/lumen/instincts.yaml`:

```
python3 research/rule_lint.py /home/lumen/instincts.yaml
```

Result: **0 dup-id, 2 dangling-ref, 42 polarity** findings out of 35 rules.

- **dup-id (0)**: the corpus's `id` field is already unique. Cheap, zero cost, genuinely
  useful as a pre-commit gate the day someone copy-pastes a rule block.
- **dangling-ref (2)**: both are the `reboot-gate`/`self-generated-evidence` supersessions from
  §2 — correct, intentional, already-documented history, not drift. A naive "does supersedes
  resolve" check produces a 100% false-positive rate here unless it also parses the footer's
  retirement log, which neither off-the-shelf tool would do out of the box either.
- **polarity (42)**: a bag-of-words "shared keyword + opposite never/always marker" heuristic —
  the same shape as RCG's "syntactic only" pass — returns 42 hits on 35 rules of dense
  operational prose, almost all spurious (e.g. `prompt-names-model` vs `time-check` flagged
  only because both happen to contain the word "session"). This is RCG's own benchmark lesson
  confirmed on a real, non-synthetic corpus: the cheap heuristic's recall/precision tradeoff
  that looked survivable on 62 synthetic pairs is unusable noise on real prose. Getting it
  usable needs subject-aware NLP (RCG's embeddings+judge tier) — which is the expensive half
  this ticket was already asking "is it worth it" about. It isn't, for 35 rules one person
  edits.

## 4. Recommendation

Keep the dup-id check (and, if desired, a footer-aware dangling-ref check that reads the
retirement log instead of flagging it) as a <20-line pre-commit hook on
`/home/lumen/instincts.yaml`. Do not build or adopt a keyword/polarity contradiction pass, a
Neo4j deployment, or an LLM-judge pipeline for a 35-rule, single-author, already-governed
corpus — the complexity (and, for RCG, the recurring per-pair reasoning-call cost) is not
proportionate to a problem the corpus's own `supersedes` field plus the
`consolidation-proposal-only` review gate already covers. No ticket to file; this is a
negative result with a cheap, already-written fallback (`research/rule_lint.py`'s dup-id
check) if drift is ever actually observed in practice.
