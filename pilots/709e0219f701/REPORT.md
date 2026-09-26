# PILOT 709e0219f701 — Defer InferenceBench integration until integration path is clarified

CARD.md ships with no hypothesis, no primary metric, no fixed sample, and no
baseline/treatment commands (all four sections are the unfilled template, see
CARD.md:24-54) — per kill criterion "Baseline does not reproduce -> stop, report
the baseline defect," there is no baseline to run. The proposed change
("Do not integrate InferenceBench into lumen-signal-engine. Instead, run
InferenceBench as a standalone nightly benchmark against LiteLLM endpoints,
logging results to a JSON file for later analysis," CARD.md:83) still has no
consumer for that JSON log: `grep -rn InferenceBench` across this worktree plus
the granted dirs (agents/work-queue, mailbot, pokemon, bin, foreman, kb,
projects/nest) returns nothing outside this pilot's own CARD.md/REPORT.md — no
eval harness, no lumen-model-watchdog hook, no scheduled job references it. The
original defer rationale (CARD.md:19: "we lack an eval harness to act on
results; defer until we define integration points") is unchanged: nothing in
this pilot window defined an integration point or built the harness that would
consume nightly benchmark output, so there is nothing new to measure.

RECOMMENDATION: SKIP
No baseline/treatment defined in CARD.md; no eval harness or consumer exists for InferenceBench output (0 repo references outside pilot metadata); original defer condition unresolved.
