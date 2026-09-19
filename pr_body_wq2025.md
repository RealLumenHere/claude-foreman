wq#2025 (wq#2023 verify-pass CONTRADICT of wq#2022/PR#644): `_NEGATION_RE`
enumerated auxiliaries and enumerated the wrong half, so the wq#2022 disclaimer
with one modal swapped still hard-rejected —
`route_conflict("Wrap the gateway in a cache; this shouldn't add a new litellm route for bge-m3.")`
returned `(True, 'bge-m3')`, same for wouldn't / couldn't / can't / mustn't.
Downstream, `minerva_proposal.py:482-487` writes a terminal
`Duplicate: LiteLLM route` decision_reason with no catch, so each of those is a
real proposal killed.

## Fix

Match the contraction, not the verb it attaches to: `\w+n't` covers every
contracted negation, present and future. Bare `not` already covered every
uncontracted `<aux> not` form that the old per-verb `does not` / `is not` /
`will not` entries were only spelling out; `cannot` is neither, so it stays
literal.

The apostrophe is required in the general arm. `reports/2025_probe_negation_contraction.py`
scores three candidates on the same corpus before one ships: the shipped list
(11/12 disclaimed adds still rejected), the obvious generalization `\w+n'?t`
(apostrophe optional, as the old `doesn'?t` entries had it — it reads
"persistent" and "different" as negations and drops 2 genuine route adds, a
false negative worse than the false positive being fixed), and the shipped
pattern (0 false positives, 0 false negatives).

## Verification

- `python3 ops/wq2019-proposal-reviewer-fix/test_route_conflict.py` → `OK: 15
  false positives cleared (12 of them reach the add-intent window logic), 6 true
  positives still caught`. New cases: the five wq#2023 modals plus the
  apostrophe-less (`cant`) and uncontracted (`cannot`) spellings; two new true
  positives are real adds phrased with `nt`-final words.
- `python3 reports/2023_probe_negation.py` — wq#2023's own repro, all five now
  `(False, None)`.
- `python3 reports/2025_probe_negation_contraction.py` — exits 0 only when the
  shipped pattern scores clean.
