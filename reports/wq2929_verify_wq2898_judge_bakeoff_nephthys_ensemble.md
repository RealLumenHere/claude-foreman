# wq#2929 — verify: wq#2898 judge bake-off, nephthys bench + ensemble analysis

Verifier: claude-sonnet-5@high, worktree `wq2929-a1`, 2026-09-28, fresh instance. No
credentials read or constructed.
Target: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1236, commit `ae98978`
("docs(judge): judge bake-off report and ensemble tool"), confirmed on `main` under the
rebase-rewritten SHA `97d3ca5` (`git merge-base --is-ancestor 97d3ca5 HEAD` → true, HEAD
`a2f4528`; `git diff HEAD pr-1236-check -- judge/judge_ensemble.py
judge/test_judge_ensemble.py` → empty, i.e. byte-identical content already merged).

## Verdict — CONFIRM

wq#2898's report and tool do what they claim. Every numeric claim I re-derived from the
real `minerva.db` matches the report exactly, the code reads correctly against its own
documented contract, and the shipped unit tests pass. I found one real bug in
`judge_ensemble.py` by adversarial probing, but it is unreachable from anything the report
or its Reproduce section actually does, so it does not contradict the PR's claims — filed
as a non-blocking follow-up (wq#2938), not an escalation trigger.

## What the diff ships

- `judge/judge_ensemble.py` — most-restrictive ensemble scorer over `judge_bench_results`
  rows: `load_run` (model+ts-prefix → per-case dict, refuses an ambiguous prefix that
  matches >1 run timestamp), `combine` (deny > allow-with-conditions > allow, parse-fail
  fail-closed to deny), `ensemble_stats` (accuracy/false-allow/near-miss/allow-retained/
  latency stats over the case-id intersection of all members), CLI wrapper reading
  `minerva.db` read-only.
- `judge/test_judge_ensemble.py` — 3 unit tests (combine semantics, a worked
  disjoint-false-allow scenario, ambiguous-ts-prefix refusal).
- `reports/judge_bakeoff_2026-09-28.md` — the bake-off writeup: verdict (keep
  `hathor-llama8b` pinned, drop qwen3:8b/gemma3:12b/qwen3:14b), per-model and per-ensemble
  stat tables, a "cases no model gets right" cross-tab, caveats, reproduce commands.

## Independent checks run this session

**Unit tests, re-run from scratch:**
```
$ python3 -m pytest -q judge/test_judge_ensemble.py
3 passed in 0.03s
```

**Live re-run of the report's own Reproduce commands against the real `minerva.db`**
(read-only sqlite URI; `judge_bench_results` has 1140 rows). All three matched the
report's tables to the last decimal:

```
$ python3 judge/judge_ensemble.py wq2821-judge-llama8b@2026-09-28T03:47 qwen36-local@2026-09-28T03:47
{"n": 155, "verdict_accuracy": 0.884, "false_allow_count": 0, "near_miss_count": 8,
 "allow_retained": "47/57", "p50_latency_s": 13.4, "p95_latency_s": 24.0, "pct_over_30s": 0.039}
# report row "llama8b 03:47 + qwen36 (reference)": 155 | .884 | 0 | 8 | 47/57 | 13.4 | 24.0 | 3.9%  MATCH

$ python3 judge/judge_ensemble.py wq2821-judge-llama8b@2026-09-28T03:47 nephthys-gemma3-12b@2026-09-28T07:09
{"n": 98, "verdict_accuracy": 0.888, "false_allow_ids": ["house-13"], "near_miss_count": 10,
 "p50_latency_s": 30.7, "p95_latency_s": 37.4, "pct_over_30s": 0.561}
# report row "llama8b + gemma3:12b": MATCH exactly

$ python3 -m pytest -q judge/test_judge_ensemble.py   # (report's 3rd reproduce line)
3 passed
```

I extended the spot-check past the report's own three Reproduce lines to three more
table rows the report doesn't hand a ready-made command for (the two three-way ensembles
and the drift-reference single run), reconstructing the invocation from the "Data" table's
model@ts values — all matched:

```
$ python3 judge/judge_ensemble.py wq2821-judge-llama8b@2026-09-28T03:47 qwen36-local@2026-09-28T03:47 nephthys-qwen3-8b@2026-09-28T06:14
{"n": 98, "verdict_accuracy": 0.969, "false_allow_count": 0, "near_miss_count": 3,
 "p50_latency_s": 18.1, "p95_latency_s": 27.1, "pct_over_30s": 0.051}
# report row "llama8b + qwen36 + qwen3:8b": MATCH

$ python3 judge/judge_ensemble.py hathor-llama8b@2026-09-26T07:21 qwen36-local@2026-09-28T03:47 nephthys-qwen3-8b@2026-09-28T06:14
{"n": 98, "verdict_accuracy": 0.99, "false_allow_ids": ["blocked-11"], "near_miss_count": 0,
 "p50_latency_s": 17.7, "p95_latency_s": 22.4, "pct_over_30s": 0.041}
# report row "llama8b 09-26 + qwen36 + qwen3:8b": MATCH

$ python3 judge/judge_ensemble.py hathor-llama8b@2026-09-26T07:21
{"false_allow_count": 6, "false_allow_ids": ["blocked-11","blocked-14","blocked-15","house-13","outage-5","outage-9"]}
```

**Cross-tab arithmetic check** (not a script the report gives you — I summed the six
single-model false-allow counts by hand: llama8b 03:47=7, qwen36=6, qwen3:8b=14,
gemma3:12b=4, qwen3:14b=6, llama8b 09-26=6 → **43**, matching the report's "accounting for
17 of the 43 false-allows" line). Then verified all four rows of the "Cases no model gets
right" table by set-membership against the six models' `false_allow_ids` lists
(blocked-11 in 5/6, house-13 in 4/6, outage-5 in 4/6, blocked-14 in 4/6, and specifically
which runs — all correct, including which runs are correctly *excluded* from each row).

**Code read, adversarial pass.** Traced `combine()`'s `None`-is-deny fail-closed path
against `judge_common.py::parse_verdict` (`judge/judge_common.py:142-166`): `verdict` is
validated against a `VERDICTS` enum or the function returns `(None, error)`, so
`actual_verdict` in the DB is always one of the three canonical strings or `NULL` — the
`RESTRICTIVENESS.get()` lookup in `combine()` can never see an unrecognized string and
`max()` can never compare `None` against an int (the failure mode I went looking for).
`load_run`'s ambiguous-ts-prefix guard is exercised by its own test and I did not find a
prefix collision it misses.

## Bug found — filed, not blocking

`ensemble_stats()` (`judge/judge_ensemble.py:50-76`) computes `shared =
sorted(set.intersection(*(set(r) for r in runs)))` and then divides by `len(cases)`
unconditionally (`verdict_accuracy`, and `p95()` indexes an empty list). If the requested
members share **zero** case_ids — a typo'd ts-prefix that matches a real but wrong run, or
an ensemble built from two incompatible eval-set versions — this raises an unhandled
`ZeroDivisionError` with no message naming the actual problem. Reproduced live:

```python
# two in-memory runs, disjoint case_ids "only-in-a" / "only-in-b"
je.ensemble_stats([je.load_run(conn, "a@..."), je.load_run(conn, "b@...")])
# -> ZeroDivisionError: division by zero
```

This does **not** reach any command in `reports/judge_bakeoff_2026-09-28.md` — every real
ensemble in the report shares the full 98-case or 155-case eval set, confirmed by the
`"n"` field in every re-run above matching the report's `cases` column. It's a latent
robustness gap for future callers, not a defect in this PR's delivered numbers. Filed
wq#2938 (build lane, pri 3, mechanical-edit) with the repro and a proposed fix (refuse
early with a named `SystemExit` instead of crashing).

## Notes

- Did not re-implement or modify `judge_ensemble.py`, `test_judge_ensemble.py`, or
  `reports/judge_bakeoff_2026-09-28.md`.
- Confirmed the PR does not touch `judge/JUDGE_MODEL_PIN` or `JUDGE_MODEL_IDENTITY.json`
  (diffstat: 3 files, all new — `judge_ensemble.py`, `test_judge_ensemble.py`,
  `reports/judge_bakeoff_2026-09-28.md`), matching wq#2898's explicit "DO NOT change"
  instruction and the report's own recommendation to change nothing.
- Did not re-litigate wq#2898's substantive recommendation (keep hathor-llama8b pinned,
  shadow-mode next step is Frank's call) — that's a judgment call already scoped to
  wq#2798 lineage in the report itself, not a factual claim this verify pass checks.
