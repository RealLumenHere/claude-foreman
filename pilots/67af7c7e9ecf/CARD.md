# PILOT 67af7c7e9ecf — Add semantic uncertainty-guided routing to Lumen agent pipelines

Experiment card. Generated 2026-09-24T09:15:00+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `67af7c7e9ecf` |
| Deferred | 2026-08-18 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/67af7c7e9ecf/REPORT.md` |

## Why this is a pilot and not a research prompt

The concept is promising but requires answering whether the metric can be computed efficiently on available models (e.g., via logprobs) and whether it improves routing without adding latency; a pilot on one pipeline is needed before adoption.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis
(Filled in by the runner, orch-burn-C / wq#2446, 2026-09-24 before any scored call, per this card's "edit it before the run" clause: the card was generated blank. Original: data/CARD.blank.orig.)

A logprob-derived uncertainty score U = 1 - exp(mean token logprob of the answer), taken from one greedy sample with no extra generations, separates wrong from right answers on a fixed factual-QA sample with AUROC >= 0.70; routing the 30% most uncertain questions from the weak model (qwen36-local) to the strong model (deepseek-v4-flash) beats escalating a random 30% by >= 5 accuracy points; and requesting logprobs adds <= 10% to median request latency.

## Primary metric
AUROC of U for predicting an incorrect answer (dimensionless), on qwen36-local, treatment arm, 200 questions. Win: AUROC >= 0.70.
Secondary (reported, not individually gating): (a) accuracy gain of uncertainty-routed vs random-30%-expected escalation, in points; (b) median latency ratio treatment/baseline; (c) capability matrix of which LiteLLM groups return logprobs (probe: /tmp/orch-burn-C/wq2446_lp_probe.py, output in the report).
Verdict rule, fixed now: ADOPT if AUROC >= 0.70 AND routed gain >= 5 pts AND latency ratio <= 1.10. SKIP if AUROC < 0.60. Anything else WATCH.

## Fixed sample
data/sample.jsonl: 200 rows, TriviaQA rc.nocontext validation rows 0-199 (HF datasets-server), sha256 005ee24de2177a10ad988744b532184c87a6ff4972ced4e9836fa8184a2ac3bf. Both arms and both models see all 200. Grader (fixed, same for every arm): lowercase, strip punctuation and articles, correct if any normalized alias is contained in the normalized answer.
Prompt: system 'Answer with only the answer, no explanation.', user = question, temperature 0, max_tokens 24, through the LiteLLM gateway localhost:4000, one request at a time.
NODE DEVIATION: the table says hathor. Hathor is not usable: at pilot start its load average was ~12 and all four hathor-backed LiteLLM groups timed out at 100 s in the capability probe, so any latency there is void. The run is API calls only, driven from Lumen, against qwen36-local (isis llama.cpp :8095, idle) and deepseek-v4-flash.

## Baseline (measure FIRST)
Today's behaviour: fixed model assignment, no uncertainty signal. Accuracy and median latency of each model alone, logprobs off.

```
python3 /home/lumen/foreman/pilots/67af7c7e9ecf/run.py baseline
```

## Treatment
Identical requests with logprobs=true, top_logprobs=5. Compute U per answer, AUROC, the 30% routing simulation (offline from the collected answers, no extra calls) and the latency ratio.

```
python3 /home/lumen/foreman/pilots/67af7c7e9ecf/run.py treatment
python3 /home/lumen/foreman/pilots/67af7c7e9ecf/analyze.py
```

Extra kill criterion: if a model returns no logprobs, stop that model's arm and report it (capability finding, not a failure).

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen currently routes tasks to models via LiteLLM with fixed model assignments per service (e.g., lumen-fleet-* services use specific models) and has no explicit uncertainty-based escalation; verification is triggered by separate mechanisms like minerva_verify.

## Proposal — proposed change

Implement a lightweight semantic uncertainty metric (e.g., based on logit entropy or embedding distance between sampled responses) as a library, and integrate it into the LiteLLM gateway or a new routing service to decide when to escalate to a more capable model or trigger verification.

---

The report at `/home/lumen/foreman/pilots/67af7c7e9ecf/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
