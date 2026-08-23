# wq#849 — Gate the Judge Construct That Actually Ships

ticket: wq#849 | agent: claude-web-633-849-7b80f2 | date: 2026-08-23 PT | deps: none

## VERDICT: **The shipped judge and the harness judge are two different instruments. Split them.**

## 1. The divergence

The R1-ADDENDUM §B (wq#188, Frank-confirmed 2026-07-13) specifies a pairwise A/B judge with
both-orders voting, provenance stripping, formatting normalization, compact rubric, and
tie-prime. This is the protocol the wq#191 harness tested.

The shipped code (`duet_prompts.JUDGE_SYSTEM` in duet_prompts.py, called by `judge_call()`
in duet.py) is a single-call trajectory judge that emits:
```json
{"converged": true|false, "quality_delta": -1|0|1, "stop_recommend": true|false, "reason": "..."}
```
No both-orders. No provenance stripping. No formatting normalization. No rubric anchor.
No tie-prime. The addendum explicitly says "replaces Q4 single-call design" — the shipped
code IS the Q4 design.

Full comparison:

| Dimension | R1-ADDENDUM §B (harness) | Shipped JUDGE_SYSTEM |
|---|---|---|
| Call pattern | Both-orders double-call (2 calls/check) | Single call |
| Input shape | Two anonymous responses A/B | Recent turns + current vs previous artifact |
| Verdict | Per-criterion rubric + overall ±1/0 | converged + quality_delta + stop_recommend |
| Position defense | Order swap eliminates bias | None |
| Provenance | Stripped ("Response A/B") | Shows A/B turn history |
| Formatting | Normalized to plain text pre-judge | No normalization |
| Rubric | 3-5 criterion anchor w/ written anchors | "Judge improvement trajectory, not absolute" |
| Tie-prime | Present ("same things = TIE") | Absent |
| Purpose | Model quality calibration | Duet loop stop signal |

## 2. Why the wq#191 FAIL does not transfer

wq#191 found gpt-oss-120b scored 0.32 directional agreement (threshold 0.80) and 0.68
tie rate (threshold 0.20). Root cause: the harness rubric's tie-prime ("If A and B say the
same things, they are a TIE regardless of styling") collapsed discrimination on math pairs
where the fallacy variant reached the same final answer. Diagnostic: removing the tie-prime
raised agreement from 0/17 to 14/17 on those pairs.

This failure mechanism is harness-specific:
- The tie-prime exists only in the harness rubric. The shipped JUDGE_SYSTEM has no tie-prime.
- The both-orders consensus amplifies the tie-prime's effect (a tie in either order forces
  the verdict to 0). The shipped judge makes one call.
- The pairwise shape (which-is-better) is vulnerable to same-answer-different-reasoning
  confusion. The trajectory shape (is-this-improving) compares artifacts against their own
  prior version, not against each other.

So: the harness is broken (tie-prime defect), but the breakage doesn't propagate to prod.

## 3. The shipped judge is also unmeasured

No gate has ever run against `JUDGE_SYSTEM` as shipped. The 0.82-0.91 v4-flash directional
figure is from literature, not local measurement. Further: wq#189 logged 0 judge rows
(degeneration stop pre-empted the judge in every run). The shipped judge has never fired in
prod. The gap is procedural, not yet consequential — but it means we have no evidence for
or against the shipped prompt's quality.

## 4. Which construct is canonical? Both, for different jobs.

The addendum's pairwise protocol and the shipped trajectory judge serve different functions:

**Trajectory judge** (shipped `JUDGE_SYSTEM`): answers "is the artifact still improving?
should the loop stop?" This is a convergence detector, not a preference ranker. It sees the
conversation history, compares current to previous artifact, and makes a continuation
decision. Position bias is less relevant because it's not comparing two anonymous
alternatives — it's tracking a trajectory. Single-call is the right shape here.

**Pairwise judge** (addendum §B protocol): answers "which of these two responses is better?"
This is for model-quality calibration (is gpt-oss-120b a competent judge? is model X better
than model Y for persona tasks?). Position bias is the dominant threat, both-orders is the
canonical defense. This is the instrument for roster decisions, not duet's inner loop.

The addendum's §B was written as if one judge protocol serves both roles. It doesn't. The
trajectory role needs convergence detection (stop_recommend, quality_delta trajectory); the
calibration role needs discrimination accuracy (directional agreement, style bias, position
consistency). Different inputs, different outputs, different threats, different defenses.

## 5. Recommendation: split, gate each independently

### 5a. Amend R1-ADDENDUM §B

Rebind §B to the pairwise calibration instrument only. Add a new §B2 for the trajectory
judge. The trajectory judge spec should be:
- Single-call (as shipped)
- Input: recent turns + current artifact + previous artifact snapshot
- Output: `{converged, quality_delta, stop_recommend, reason}` (as shipped)
- Gate criteria: measured against logged duet trajectories where the human-observable
  stopping point is known. Does stop_recommend fire within ±1 round of the right moment?
  Does quality_delta track the actual improvement trajectory?
- Model: v4-flash (current default, cross-family from builder v4-pro)

### 5b. Gate the trajectory judge (new ticket)

Run the shipped JUDGE_SYSTEM against logged duet trajectories. If wq#189 logged zero
judge rows, we need synthetic trajectories: take 3-5 completed duet runs from duet.db,
replay the turn sequence through the judge at each even round, compare stop_recommend
against the round where the run actually stopped (or where a human would have stopped).
This is cheap (small number of judge calls on existing transcripts).

### 5c. Re-gate the pairwise judge (new ticket)

Fix the tie-prime rubric defect found by wq#191's diagnostic. Re-run BOTH halves (style
bias + directional) with the revised rubric. The diagnostic showed 14/17 correct with
tie-prime removed — promising but untested against the style-bias half (where removing the
tie-prime might reintroduce style bias). Only a full re-run on both halves decides.

### 5d. v4-flash baseline (new ticket, cheap)

Run the same pairwise harness with JUDGE_MODEL=deepseek-v4-flash on the same 25 clear
pairs. v4-flash is the live default judge by inheritance, not by evidence. Same bench,
same pairs, different model — closes the gap in one run.

## 6. Code paths audited

- `duet_prompts.py`: JUDGE_SYSTEM (trajectory, single-call), DEFAULT_JUDGE = "deepseek-v4-flash"
- `duet.py:judge_call()` (line 242): single call to JUDGE_SYSTEM, parses JSON, fail-open
- `duet.py` DDL: `duet_judge` table has `order1_winner`, `order2_winner`, `agreed` columns
  from the addendum DDL diff — but `judge_call()` never populates them. Schema was amended,
  code was not.
- `personas/qa/judge_gate_gptoss.py`: pairwise A/B harness, both-orders, tie-prime rubric.
  Tests a DIFFERENT construct than what duet runs.
- `duet_prompts.py` line 100: DEFAULT_INSTRUCTOR = "cerebras-oss-120b" (the critic, NOT the
  judge). The wq#191 report correctly identified this premise error.

## Reproduce

```
# Audit the divergence yourself:
diff <(grep -A20 'JUDGE_SYSTEM' /home/lumen/duet/duet_prompts.py) \
     <(grep -A20 'both-orders\|double-call' /home/lumen/agents/work-queue/reports/agent_duet_r1_architecture.md)

# Check judge_call never populates addendum columns:
grep -n 'order1_winner\|order2_winner\|agreed\|length_flag' /home/lumen/duet/duet.py

# Count prod judge firings:
sqlite3 /home/lumen/duet/duet.db "SELECT COUNT(*) FROM duet_judge WHERE converged IS NOT NULL;"
```
