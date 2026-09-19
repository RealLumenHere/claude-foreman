# wq#2020 — hallucination guard on the local stack

Proposal a60f17e4b517 was deferred on two reasons. Both were wrong:

- "logits/hidden states LiteLLM may not expose" — isis ollama returns `logprob` +
  `top_logprobs` per token on `/v1/chat/completions` with `logprobs:true`. Probed live
  2026-09-19 (`qwen3.6:35b-a3b` -> `"Paris"`, logprob -0.0047, top_logprobs `Paris`/` Paris`).
- "the module does not exist" — circular; `harness/hallucination_guard.py` is the module.

## What landed

| | signal | code |
|---|---|---|
| A | token confidence: runs of >=2 tokens below p=0.35 are guessed spans; `guarded_generate` regenerates once telling the model to drop them | `low_confidence_spans`, `guarded_generate` |
| B | self-consistency: N samples, mean pairwise Jaccard over each sample's *specifics* (numbers, identifiers, paths, proper names); divergent specifics are named | `agreement`, `self_consistency` |
| C | verify-gate extension: cited `file:line` excerpt vs the worker's claim, verdict sampled N times (B), scored by first-token probability (A) | `check_claim`, `claim_check`, `acceptance.verify(claim_check=)` |

Wiring: `fworker.verify_done` passes `hallucination_guard.claim_checker()` to
`acceptance.verify`, after the mechanical replay is clean. The claim is the evidence
entry's optional `"claim"` sentence, else the criterion text. `verify-evidence
--claim-check` is the opt-in self-check. The Stop hook never runs it (no local inference
on the worker's stop path).

Decisions (recorded, not asked):
- default mode **advisory** (`FOREMAN_HALLUCINATION_GUARD=off|advisory|enforce`): verdicts
  land in `result["claim_check"]` and the fworker log; only `enforce` fails the gate. A
  probabilistic judge should earn its blocking role from advisory data first.
- unreachable guard model -> `inconclusive`, never blocks in any mode: a down free tier
  must not stall the fleet.
- direct to isis ollama (`FOREMAN_GUARD_BASE_URL`), not via LiteLLM: logprobs pass-through
  is verified against ollama itself.
- `unstable` (split verdicts or low confidence) is flagged like `unsupported`: divergence
  is the hallucination signal.

## Live proof (isis `qwen3.6:35b-a3b`, n=3, 2026-09-19)

Source = `harness/acceptance.py:74-96` (the real `replayable()` function).

| claim | verdicts | agreement | confidence | status |
|---|---|---|---|---|
| "replayable() rejects commands containing shell metacharacters" (true) | supported x3 | 1.0 | 0.9982 | **supported** |
| "replayable() retries a failed command three times with exponential backoff and caches the result in Redis" (**seeded hallucination**) | unsupported x3 | 1.0 | 0.9998 | **unsupported** |
| "replayable() decides whether a criterion command is safe to re-run" (true but vague) | supported, unsupported, supported | 0.67 | 0.58 | **unstable** (flagged) |

The vague true claim being flagged is the cost of a conservative judge: it is why the
default is advisory. Reproduce:
`python3 harness/hallucination_guard.py claim --source harness/acceptance.py:74-96 --claim "..."`.

Signals A and B on `ask`: the control ("capital of France") gave agreement 1.0, no
spans. A fabricated-entity prompt ("Zorblax 4.2" CVE) was answered correctly ("no such
CVE"), so this model did not hallucinate it and neither signal fired; a second obscure-fact
prompt hit the 90 s read timeout (ISSUE-LOG #435, fixed). A and B are covered by
deterministic tests only, not by a live seeded catch.

## Tests

`harness/tests/test_hallucination_guard.py`, 20 tests, no network: transport shape
(logprobs requested, n requests, timeouts -> OSError), A spans and regeneration, B
stable-vs-invented, C seeded hallucination flagged / hesitant -> unstable / down ->
inconclusive, and the verify gate in each mode. `test_evidence_gate.py`: 46 pass; the 4
`stop_hook` failures are environmental (ISSUE-LOG #436), not from this change.

## Not done

Workers are not yet prompted to write a `"claim"` sentence per citation, so today the
claim falls back to the (often generic) criterion text. Flip to `enforce` after a week of
advisory verdicts in the fworker log.
