# PILOT 9d95766f5f43 — Implement latent-guided explore-exploit prompt sampling in prompt-engine

Experiment card. Generated 2026-09-11T09:11:16+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `9d95766f5f43` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/9d95766f5f43/REPORT.md` |

## Why this is a pilot and not a research prompt

The technique is research-stage and Lumen lacks an eval harness to measure whether adaptive prompt sampling improves proposal quality; defer until a baseline metric and simpler adaptive mechanism are proven.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

An explore-exploit sampler (Beta-Bernoulli Thompson sampling) over the topic arms
that feed Lumen's proposal pipeline yields **at least 2.0 more accepted proposals
per 100 prompt draws** than the static uniform-over-arms sampling
`prompt_engine.py:343-351` does today, replayed against Lumen's own labeled
proposal-outcome history.

Falsifier: if the paired mean gain is below +2.0 accepted/100, or its 95% paired
bootstrap CI includes 0, adaptive sampling does not pay for itself at Lumen's
arm-count and horizon, and the answer is WATCH or SKIP, not ADOPT.

Surrogate arms, stated before the run: the proposal asks to reweight the eight
prompt-engine topic banks (`prompt_engine.py:332-341`). Lumen's outcome record
carries **no bank-level attribution** — a proposal row points at
`classified_intel.id`, and only 5 of 20,474 intel rows reference a `pe_*` research
artifact (3 proposals, 0 accepted), so no per-bank reward exists to measure. The
pilot therefore tests the *mechanism* on the arm dimension that is labeled —
`classified_intel.category` (primary) and `classified_intel.source` (sensitivity) —
and reports the missing attribution as a separate, hard buildability finding.

## Primary metric

**Accepted proposals per 100 prompt draws**, horizon 300 draws, mean over 2000
paired simulated runs. Unit: count per 100 draws (equivalently 100x acceptance
rate). "Accepted" = `proposals.status` in
(`approved`,`planned`,`staged`,`applied_unverified`,`deployed`,`shipped`,`piloting`) —
the live decision written by production, not by this pilot.

**Win condition, fixed before the run:** treatment mean >= baseline mean + 2.0
accepted/100 AND the 95% percentile bootstrap CI of the paired per-seed difference
excludes 0. Reported alongside, not decisive: the oracle ceiling (always play the
best arm), the historical-mix baseline, horizon-100 sensitivity, the source-arm
sensitivity run, and a first-half/second-half stationarity check.

## Fixed sample

`/home/lumen/foreman/pilots/9d95766f5f43/sample.json` (gitignored; rebuild with
`build_sample.py`), sha256 recorded in `REPORT.md`.

- 1400 proposal rows: every `proposals` row with a non-null `source_intel_id`,
  joined to its `classified_intel` row. Columns extracted, and only these:
  proposal id, created_at, status, impact, intel category, intel source.
- Derived per row: `accepted` (0/1) by the status set above.
- 7 category arms (n = 804/243/156/107/62/15/13), 1400 rows total,
  2026-04-22 .. 2026-09-11.
- Both arms of the experiment draw rewards from this one file, same seeds.

Extract built by `build_sample.py` on Lumen: no credential columns, no proposal
text, no `minerva.db` copy.

## Baseline (measure FIRST)

Static sampling as `prompt_engine.py` does it today — round-robin interleave over
banks then `random.shuffle` — which is uniform over arms. Reward for a draw on arm
a is a Bernoulli draw from arm a's empirical historical pool.

```
python3 pilots/9d95766f5f43/run_pilot.py --arm baseline --out results_baseline.json
```

## Treatment

Beta-Bernoulli Thompson sampling over the same arms, same horizon, same seeds,
same reward pools; Beta(1,1) priors, posterior updated on each observed outcome.
No prod path is touched: this is an offline replay script in the pilot directory.

```
python3 pilots/9d95766f5f43/run_pilot.py --arm treatment --out results_treatment.json
```

**Node:** the card names hathor. Both arms here are deterministic seeded CPU
arithmetic over a fixed extract — no model, no GPU, no network — so the executing
node cannot change the metric; and `ssh` is not on this worker's Bash allowlist
(`harness/worker-settings.json`), so the run executes on Lumen. Recorded here
rather than silently.

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

Lumen's prompt-engine (lumen-prompt-engine.service) fills the research queue using static topic banks with no auto-adaptation; this is a documented gap.

## Proposal — proposed change

Add a standalone 'latent-guided explore-exploit' sampler that periodically analyzes recent proposal acceptance/impact data and recommends revised topic weights. Run it as a separate dry-run script on a timer, initially without modifying the live prompt-engine path.

---

The report at `/home/lumen/foreman/pilots/9d95766f5f43/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
