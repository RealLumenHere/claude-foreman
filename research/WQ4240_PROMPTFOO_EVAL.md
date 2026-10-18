# wq#4240 — EVALUATE: promptfoo for LLM eval / model quality benchmarking

**VERDICT: ALREADY DEPLOYED. Everything this ticket asks for — promptfoo, providers
pointed at LiteLLM `http://localhost:4000/v1`, a golden dataset built from real fleet
tickets, LLM-judge grading of worker output quality — landed under wq#1300 and is live
in-tree at `harness/evals/worker-quality/`. No new install, no new config. The one piece
genuinely missing (Gitea Actions PR gating) doesn't apply: this repo has no Gitea Actions
workflows at all, on any ticket, so there is nothing to wire a quality gate into.**

## What's already running (confirmed this session, read-only)

- `harness/evals/worker-quality/promptfooconfig.yaml:9-16` — `providers` is
  `openai:chat:${WQ1300_JUDGE_MODEL:-claude-sonnet-5}` with
  `apiBaseUrl: ${LITELLM_BASE_URL:-http://localhost:4000}/v1`, i.e. exactly the LiteLLM
  gateway address this ticket specifies.
- `harness/evals/worker-quality/gen_dataset.py` (`load_real_cases`, per
  `harness/evals/worker-quality/README.md:20-28`) builds the golden dataset from real
  fleet tickets: `tasks.notes` from `queue.db`, labeled by `tasks.reopened_count` (the
  wq#841 durable rework signal), plus hand-authored synthetic fixtures for shapes real
  notes rarely spell out.
- The "`llm-rubric` for code quality judging" ask is implemented as a model-graded judge:
  `judge_prompt.md` feeds a ticket + a worker's done-claim to the judge model, scored by
  `grader.py` (wired as `defaultTest.assert: type: python, value: file://grader.py` in
  `promptfooconfig.yaml:18-21`) against ground truth, aggregated by `analyze_results.py`.
  This measures what wq#4240's ask ("judge code quality") actually cashes out to on this
  fleet: catching false-complete done-claims (ISSUE-LOG #17), not a generic style score.
- Run path is `run_eval.sh`, never `promptfoo eval` directly — it enforces a loopback-only
  guard on `LITELLM_BASE_URL` (`README.md:48-51`) so the suite can't accidentally fire
  across the wifi bridge to a router, and caps concurrency
  (`WQ1300_MAX_CONCURRENCY`, default 2, max 4).
- Already logged in `/home/lumen/kb/ISSUE-LOG.md` row 105 (dispatched ahead of its stated
  dependency wq#1289, deliberately decoupled — ground truth comes from
  `tasks.reopened_count` instead of waiting on wq#1289's evidence.json shape).

## The one gap: Gitea Actions PR gating

This ticket's "wire into Gitea Actions to block PRs if quality drops" has no landing
spot: `find . -path '*workflows*' -o -iname '*.gitea*'` (this session, repo-wide) returns
nothing, and no other ticket in this tree has added Gitea Actions workflows either —
PRs land via `lumen-pr` + harness review gates (`fworker.py verify-evidence`,
evidence.json), not CI-triggered quality scores. Wiring a promptfoo gate into a CI system
that doesn't exist here would mean building Gitea Actions from scratch as a prerequisite
— a separate, much larger ticket, not an "EVALUATE" one. The existing evidence-gate
Stop-hook mechanism (acceptance.json/evidence.json, this same session's own protocol)
already blocks a ticket from closing on unverified claims; promptfoo's false-complete-rate
metric (`README.md:81-87`) measures that gate's effectiveness after the fact, which is
the role it's already playing.

## Recommendation

Close this ticket as evaluated-already-deployed, no new action. If a future need
specifically requires promptfoo gating *new PRs* pre-merge (as opposed to scoring the
done-claim critic after the fact), re-open scoped to "add Gitea Actions to this repo" as
its own prerequisite ticket — don't re-run this install/config comparison, it's settled.
