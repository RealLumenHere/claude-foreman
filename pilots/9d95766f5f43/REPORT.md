# PILOT REPORT 9d95766f5f43 — Latent-guided explore-exploit prompt sampling

Ticket wq#1791. Run 2026-09-12. Card: `CARD.md` (same directory).
Question the defer asked: is adaptive (explore-exploit) prompt sampling worth
building, given Lumen had no baseline metric and no proven simple mechanism?

**Two answers, and they point opposite ways.** The mechanism works: a simple
explore-exploit sampler beats Lumen's static uniform sampling by +3.8 accepted
proposals per 100 draws in simulation and by +9.4/100 on a time-ordered holdout of
real proposals. The proposal as written does not: the topic banks it wants to
reweight have produced **0 accepted proposals in five months**, so there is no
reward signal to steer and nothing for a reweight to improve.

## What was measured

Fixed sample, both arms: `sample.json` (gitignored, rebuild with `build_sample.py`),
sha256 `6455685a8338ee17c5c66b0787be5227869d42513de478102d757ce4f0643cc6`
— 1400 proposal rows (2026-04-22 .. 2026-09-11), each joined to its
`classified_intel` parent; columns id/created_at/status/impact/category/source only.
206 of 1400 accepted (14.7%). Acceptance labels are production decisions
(`proposals.status`), not written by this pilot.

Arms: `classified_intel.category`, 7 arms — paper 804 rows @ 0.138, tool 243 @ 0.144,
news 156 @ 0.237, technique 107 @ 0.159, framework 62 @ 0.065, hardware 15 @ 0.067,
tools 13 @ 0.077.

### Primary metric — accepted proposals per 100 draws, horizon 300, 2000 paired seeds

Common random numbers across policies, so each per-seed difference is paired
(`run_pilot.py:52-60`). Full output: `results.json`.

| policy | accepted/100 | delta vs static | paired 95% CI |
|---|---|---|---|
| **baseline** static uniform over arms (`prompt_engine.py:343-351`) | **12.653** | — | — |
| baseline, historical-mix weights | 14.648 | +1.994 | [1.883, 2.103] |
| **treatment** Thompson sampling (Beta-Bernoulli) | **16.455** | **+3.802** | **[3.656, 3.942]** |
| treatment, epsilon-greedy (eps=0.1) | 17.880 | +5.226 | [5.027, 5.431] |
| oracle, always play the best arm | 23.797 | +11.143 | [11.023, 11.267] |

Pre-registered win condition — treatment >= baseline + 2.0 accepted/100 **and** the
paired bootstrap CI excludes 0 — **passes** (+3.802, CI [3.656, 3.942]).

The simpler mechanism the defer note asked for wins the treatment arm: epsilon-greedy
beats Thompson by +1.4/100 at this horizon. With 7 arms and 300 draws, Thompson is
still paying for exploration that epsilon-greedy has already stopped paying for.

### Sensitivities

| variant | Thompson delta | CI | verdict |
|---|---|---|---|
| horizon 100 (`results_h100.json`) | +1.887 | [1.669, 2.096] | **below the +2.0 bar** |
| feedback delayed 50 draws (`results_delay50.json`) | +3.097 | [2.959, 3.239] | passes |
| arms with n>=50 only (`results_minn50.json`) | +3.571 | [3.427, 3.719] | passes |
| source arms, n>=20 (`results_source.json`) | +7.688 | [7.500, 7.872] | passes |

Delay is implemented for Thompson only; the epsilon-greedy column in
`results_delay50.json` is therefore the zero-delay number, not a delayed one.

Short horizons are where this fails. At 100 draws the gain misses the pre-registered
bar, so a sampler that is reset, re-seeded, or re-armed often enough to sit near a
100-draw horizon is not worth building.

### Holdout transfer — the number that is not a simulation

`holdout.py` drops the assumption that historical rates are true rates. It learns the
arm ranking on the older half of the record and scores it on the newer half's real
rows only (split 2026-08-09T11:16:06Z, 700/700 rows, arms with n>=20).

| arms | baseline uniform | exploit train-best | delta |
|---|---|---|---|
| category (news, paper, technique, tool) | 15.94/100 CI [15.20, 16.65] | 25.33/100 CI [24.42, 26.17] | **+9.39/100** |
| source (GitHub, HuggingFace, The Decoder, arXiv) | 12.76/100 CI [12.11, 13.43] | 22.83/100 CI [22.03, 23.62] | **+10.07/100** |

The best arm learned on the training half was still the best arm on the test half in
both cuts (`news` 0.216 -> 0.248; `The Decoder` 0.217 -> 0.231), and the confidence
intervals do not overlap. The ranking is stable enough to exploit; `paper` drifted
down (0.163 -> 0.112) but never led. Small arms (hardware n=15, tools n=13) are pure
noise and a deployed sampler must floor them, not chase them.

## Why the proposal as written still cannot be built

The proposal wants to reweight the eight prompt-engine topic banks
(`prompt_engine.py:332-341`). There is no reward signal attached to a bank:

- 116 `pe_*` research artifacts exist in `/home/lumen/scratch/research_clean`
  (agentarch 16, mcp 19, inference 20, memory 17, observ 12, security 12,
  researchinfra 10, automation 10).
- 5 of 20,474 `classified_intel` rows reference a `pe_*` artifact.
- **0 proposals** trace to a `pe_*` artifact. Only 4 of 1404 proposals lack an intel
  parent at all, so at most 0.3% of the record could come from the research path by
  any route.

Every arm that wins above is an *ingest* arm — a feed category or a feed source —
not a bank the prompt engine samples. Reweighting the banks optimizes a stage that
currently contributes roughly nothing to accepted proposals; the measured win belongs
to a different knob. Building the sampler over banks first would produce a correct
implementation of a no-op.

## Recommended next step (for PLAN, not wired here)

Two tickets, in order. (1) Instrument: stamp the originating bank onto the intel and
proposal rows a `pe_*` research doc produces, so a per-bank reward exists — this is
also the only way to find out why 116 research docs yielded no proposals, which is
the larger finding. (2) Then the sampler, epsilon-greedy rather than Thompson at
these horizons, with a per-arm floor, over whichever arm dimension has a live reward.

Nothing in this pilot touched the prompt-engine path. Scripts are offline replays in
this directory.

## Run notes

Both arms are deterministic seeded CPU arithmetic over a fixed extract — no model, no
GPU, no network — so the executing node cannot move the metric. The card names hathor;
`ssh` is not on this worker's Bash allowlist (`harness/worker-settings.json`), so the
run executed on Lumen. Wall clock ~55 min, well inside the 90-minute budget.

RECOMMENDATION: WATCH

Static-sampling baseline 12.653 accepted/100 draws. Thompson +3.802/100 (paired 95%
CI [3.656, 3.942]) and epsilon-greedy +5.226/100 at horizon 300 — the pre-registered
+2.0 bar is met; holdout transfer on unseen later proposals +9.39/100 (category arms)
and +10.07/100 (source arms). But +1.887/100 at horizon 100 is below the bar, and the
proposal's own arm dimension has 0 accepted proposals out of 116 research artifacts,
so the reweight it specifies has no reward to optimize. WATCH until bank->proposal
attribution exists; the mechanism itself is proven and ready for the ingest-arm knob.
