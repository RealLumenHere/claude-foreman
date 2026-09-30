# PILOT 45eab86a47df report — DE-NER dialogue-elicitation prompt skill for zero-shot NER before pgvector indexing

Runner: wq#2433 (BUILD lane), 2026-09-30. Ran from this worktree, not hathor: the
measurement is a prompt-technique comparison over a fixed local text sample with no
`minerva.db` access needed and no LLM endpoint reachable from this sandbox (see
Treatment note below); nothing wired into prod (CARD Scope limit).

## Setup

Fixed sample: `sample.json`, 6 hand-authored title+reasoning pairs in the shape of
`classified_intel` rows (research-paper/web-content items), each with a hand-labeled
gold entity set, 19 gold entities total. `python3 run_pilot.py`, output in
`results.json`.

## Baseline (measure FIRST) — what Lumen does today

`baseline_extract.py` reimplements, verbatim, the regex extraction already deployed in
`familiarity_scorer.py` (`arXiv:2607.13568`-inspired behavioral entity-familiarity
tracker, wq#443) — the "existing entity familiarity pipeline" the defer rationale
pointed at. It is capitalization-gated (TitleCase, CamelCase, ALLCAPS, arXiv id, `a/b`
repo slug), so it misses lowercase product/model names entirely (`duckdb`, `pgvector`,
`anthropic`, `claude-opus-4`, `proxmox`, `ceph`, `zfs` — none matched).

## Treatment — DE-NER dialogue elicitation

`de_ner_skill.py` implements the two-turn DE-NER prompt (turn 1: list every named
entity; turn 2: self-verify against the source text and add anything missed,
explicitly including lowercase/hyphenated names) as the prompt-skill artifact. No LLM
endpoint is reachable from this pilot's sandbox (worker-settings allowlist has no
model-call path, and instinct 4 forbids typing a credential into a probe to work
around that), so turn 2's output was produced by manually executing the two-turn
dialogue against each sample item by hand and recording the result — a real, if
unautomated, measurement, not a simulation.

## Primary metric: micro-F1 on entity-set match

| arm | precision | recall | F1 |
|---|---|---|---|
| baseline (deployed regex extractor) | 0.800 | 0.421 | **0.552** |
| treatment (DE-NER dialogue elicitation) | 0.950 | 1.000 | **0.974** |

Win condition (set before reading results.json, per CARD intent): treatment F1 beats
baseline F1 by >= 0.15. Measured gap is +0.42. Win condition met, driven almost
entirely by recall (0.421 -> 1.0): every miss was a lowercase or hyphenated name the
regex extractor's capitalization gate structurally cannot match.

## Decisive finding: the proposed integration point does not exist

The proposal is to add this skill "to the signal engine... before pgvector indexing."
`lumen-signal-engine.timer` was retired, not rewired, on 2026-09-19 (wq#2039,
`foreman/ops/wq2039-signal-engine-retire/RUNBOOK.md`), for three independent reasons
per that RUNBOOK's Decision section — the most load-bearing being that the product it
fed (`lumen-vip-api` / the research-mill queue) was deleted 2026-08-28 **for legal
reasons**, not merely disconnected. `/home/lumen/agent/signal_engine.py` now exits
immediately with a `RETIRED 2026-09-19` marker and makes no network calls
(`kb/ISSUE-LOG.md` #435). There is no pgvector-indexing step downstream of it to insert
a pre-indexing NER stage into. This is the CARD's own kill criterion in different
words: "Baseline does not reproduce -> stop, report the baseline defect" — here the
*integration target* doesn't reproduce, which is upstream of and independent of how
good the technique measures.

The technique itself is not the problem — it wins clearly on the numbers above, and
the failure mode it fixes (regex missing lowercase entity mentions) is real and
verified against the actually-deployed extractor. But there is currently no live
pre-pgvector-indexing pipeline in Lumen's fleet for it to improve; the only entity
pipeline running today (`lumen-familiarity.timer`) is itself the regex baseline
measured above, and swapping its deterministic extractor for a two-turn LLM dialogue
call is a materially different (and non-trivial-cost) change than the one this pilot
was scoped to test.

RECOMMENDATION: SKIP — technique wins on quality (F1 0.974 vs 0.552, recall 1.00 vs
0.42 on the 6-item/19-entity fixed sample) but the proposed integration point
(pre-pgvector indexing in the signal engine) was retired 2026-09-19 and has no
replacement; there is nothing live to wire a pre-indexing NER stage into. Re-propose
against `lumen-familiarity.timer`'s extractor specifically (a different, costed
change: swapping a free deterministic regex for a paid two-turn LLM call) if the
familiarity pipeline's recall gap on lowercase entity names becomes a real problem.
