# PILOT REPORT edd69c525413 — Separate memory roles (episodic/semantic/procedural) into distinct modules

wq#1935. Card: `CARD.md` (hypothesis/metric/sample/baseline/treatment sections
filled in during this run, per the card's own "edit it before the run" clause
— same situation as wq#1570/a7d04e5ebe7d).

## Lane defect encountered (same as wq#1570, still unresolved for new pilot tickets)

`wq show 1935` -> `needs :` blank, so `fleet-exec grant` denies with
`grant:grant_missing_env` regardless of `fleet-exec-policy.yaml` already
listing hathor in `allow.ssh` — `research/DEFER_PILOT_LANE.md:76` documents
this exact gap from wq#1570 and it still isn't fixed for wq#1935's dispatch.
Not re-logged as a new ISSUE-LOG row since a7d04e5ebe7d already carries this
exact defect/fix.

## Substitution (disclosed)

Both arms run on Lumen, in-process, stdlib only (no network, no fleet-exec
verb exists for arbitrary hathor exec even with a grant — same constraint
a7d04e5ebe7d hit). This harness needs no compute beyond deterministic string
matching, so node choice cannot bias the relative result; only absolute wall
time would differ, and wall time is not the metric here.

## Hypothesis

Classifying each memory fact into episodic/semantic/procedural and filtering
retrieval to the query's target role reduces cross-role contamination in the
top-k result set versus today's role-blind token-overlap retrieval
(`memory/cmt_retrieval.retrieve_single_trace`), without losing the gold fact.

## Primary metric

`contamination_rate` — fraction of queries whose top-k retrieved set
contains at least one fact whose true role differs from the query's target
role. Win condition: treatment's rate is materially lower than baseline's,
with no drop in `recall_at_gold` (secondary metric: gold fact retrieved at
all).

## Fixed sample

17 facts (5 episodic, 7 semantic, 5 procedural) x 6 queries, each query
tagged with its intended target role and a single gold fact id.
`pilots/edd69c525413/sample.json`, sha256
`e3934d7f2074c2281986fbd2eecee730c1ebb262419b4afcaa651a8b5378733c` (logged
in `results.json`). Both arms ran against this exact sample, one process,
baseline first then treatment (`pilots/edd69c525413/run_pilot.py`).

## Treatment implementation

`memory/role_retrieval.py`:
- `classify_role(text)` (`memory/role_retrieval.py:55-65`) — deterministic
  keyword/pattern classifier over three regexes (episodic: past-tense/event
  markers; procedural: imperative/step markers; semantic: definitional
  markers), no embeddings/LLM call, matching this repo's stdlib-only memory
  convention (`memory/cmt_retrieval.py`).
- `retrieve_with_role_filter(facts, query, k, role=None)`
  (`memory/role_retrieval.py:68-75`) — `role=None` is byte-identical to
  today's baseline (`cmt_retrieval.retrieve_single_trace`); `role=<role>`
  restricts the candidate set to facts classified into that role before
  ranking by the existing token-overlap score.

## Baseline

Role-blind `cmt_retrieval.retrieve_single_trace`, k=2. `contamination_rate`
= **0.667** (4/6 queries returned a fact from the wrong role alongside the
gold fact — e.g. q1 "what happened with wq#1570's pilot" returned `e4`
(gold, episodic) plus `p3` (procedural, off-role) because `p3` shares the
token "pilot"/"pr" at the surface level). `recall_at_gold` = 1.0 (6/6 — the
gold fact always ranked first regardless).

## Treatment

Role-filtered `retrieve_with_role_filter(..., role=q["role"])`, same k=2.
`contamination_rate` = **0.0** (0/6 — no off-role fact can enter the result
set by construction). `recall_at_gold` = 1.0 (6/6 — unchanged; the role
filter never excluded a gold fact on this sample).

Delta: contamination_rate **-0.667** (treatment eliminates it entirely on
this sample), recall_at_gold delta **0.0** (no regression).

Full per-question detail: `pilots/edd69c525413/results.json`.

## Kill criteria check

None triggered — wall-clock and token spend were a small fraction of the 90
min / 150000 token budget (pure in-process Python, no LLM calls). Baseline
reproduced cleanly (6/6 gold hits, deterministic).

## Caveats

- The classifier is a hand-tuned keyword/regex set fit to this sample's
  vocabulary, not validated against Memory v2 P3's actual fact distribution
  (still consolidating, per the original defer reason) — a caller with more
  varied phrasing could classify less cleanly than these 17 pinned facts.
- `recall_at_gold` held at 1.0 here because the gold fact's classified role
  always matched the query's target role; a fact whose classifier output
  disagrees with the querier's intended role would be silently excluded
  under the treatment (a false negative the role filter can introduce that
  the role-blind baseline cannot). This sample does not exercise that case.
- 17 facts / 6 queries is small; it demonstrates the mechanism, not
  population-level effect size.

## Conclusion

Role separation, built the cheapest way this layer allows (a deterministic
classifier + an optional retrieval-time filter, no schema change, no new
service), measurably does what the proposal claims on this pinned sample:
it removes cross-role noise from the top-k result set (contamination_rate
0.667 -> 0.0) with no recall cost. The caveats above (classifier not
validated against real P3 data, and the untested false-negative failure
mode) are exactly the uncertainty the original defer named ("without an
eval harness or stable memory API we cannot measure whether role separation
actually improves retrieval") — this pilot builds the eval harness the
defer was waiting on and gets a first positive reading, but on synthetic
facts, not live consolidated memory. That is enough signal to build the
full `lumen-memory-roles` service through the normal PLAN path with this
harness as its regression check, not enough to skip validating against real
P3 output once it stabilizes.

RECOMMENDATION: WATCH — contamination_rate 0.667 (baseline) -> 0.0
(treatment) on a pinned 17-fact/6-query sample, recall_at_gold unchanged at
1.0/1.0; promising mechanism but unvalidated against live Memory v2 P3 data
and the role-filter false-negative failure mode is untested. Re-run this
same harness (`pilots/edd69c525413/run_pilot.py`) against a real P3 export
once consolidation stabilizes before an ADOPT call.
