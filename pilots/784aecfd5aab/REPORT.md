# PILOT 784aecfd5aab report — local LSP layer for code context retrieval

Runner: orch-burn-B-3f4634 (wq#2438), 2026-09-24. Card filled before the first scored call.
Ran on Lumen scratch, not hathor: hathor has no node, npm, numpy or pip3, and the sample is
`/home/lumen/minerva` code, which the card's data-handling rule keeps off pilot nodes.
Nothing was installed system-wide (pyright 1.1.414 went into an npm prefix under `/tmp`).

## What was measured

Sample: 64 top-level `*.py` files from `/home/lumen/minerva` (865,548 bytes, digest sha256
`6013983ca17d75f3aa3d9ef652cd0ce163f5b9a39e3b8129e4025a97a94bca42`), 30 functions picked by
name hash out of the ones defined once with at least 2 other uses. Each question: where is it
defined, what is its signature, who uses it. Tokens are tiktoken cl100k, a proxy tokenizer.

| arm | median tokens per question |
|---|---|
| A whole files (definition file + every file with a use) | 7,018.5 |
| **B `grep -n -w -C3` over the corpus** (the deciding baseline) | **672.5** |
| **C pyright: hover + definition + reference lines** | **112.5** |

- C / B = **0.167** (pre-registered win: <= 0.50). C / A = 0.016.
- pyright startup plus all 30 questions took 11.3 s; median 0.033 s per question.
- Reference recall against the AST ground truth: **0.811** mean (pre-registered gate: >= 0.95). Precision 0.98.
  Definition found from the first use site in 25 of 30.

## Verdict under the rule fixed in the card

ADOPT needed ratio <= 0.50 and recall >= 0.95. The ratio passes by a wide margin and the recall gate fails, so the
rule gives **WATCH**. I am not changing the rule after seeing the numbers.

## Why recall failed (post hoc, noticed after the primary numbers, not part of the verdict)

The ground truth is my design flaw, not clean. It matches by name, so it counts unrelated `.attr` uses (`re.search`,
`self.status`) and same-named local variables, and it misses `from x import f` lines that pyright correctly returns.

- 9 of 30 names (`status`, `digest`, `staleness`, `episodic_dupes_v2`, `search_v3`, `archived_ids`,
  `mark_state_projections_dirty`, `index`, `search`) are also used as attributes. Ground truth for them is not trustworthy.
- On the other 21 (no attribute uses, import lines counted as true references): recall 0.925, precision 0.99, definition
  found 19/21.
- The three low-recall cases in that subset (`drift`, `metrics`, `_lane`) each have a same-named local variable or
  parameter in the corpus (`lumen_migrate.py:103`, `drift_plan.py:221`, `lumen_state.py:621`). That is consistent with pyright being
  scope-aware where name matching is not. I did not verify each site.

## Limits

- Token counts only. No model was asked anything, so answer quality is untested.
- Python only, one 64-file corpus, one server. pyright memory use was not measured.
- Baseline B is a grep proxy for what a competent agent does, not a measurement of Lumen's own coding agents.
- The harness was edited once after the first run to also dump the reference sites; primary numbers were re-checked equal.

## If someone continues

Rerun with a scope-aware ground truth (for example `jedi` or pyright's own definition resolution per use site) and gate on
that. If recall holds at 0.95 the token result already clears ADOPT. Serving would need node on the host, which Lumen has and hathor does not.

RECOMMENDATION: WATCH — pyright arm 112.5 vs grep 672.5 median tokens (ratio 0.167, needs <= 0.50, passes); recall 0.811 vs gate 0.95 fails on a name-matching ground truth (0.925 on the 21 unambiguous names); 30 questions, token counts only.
