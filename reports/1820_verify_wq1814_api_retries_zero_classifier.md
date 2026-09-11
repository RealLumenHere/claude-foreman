# wq#1820 — Adversarial verify-pass of wq#1814 (api_retries=0 classifier fix)

**Verdict: CONFIRM** — the RCA (`reports/api_retries_zero_rca.md`) and the patch to
`harness/classifier.py` (commit `d74c426`, PR #511, already merged to `main`) hold under
independent re-run and adversarial edge-case probing. No fabricated numbers, no
regressions found.

## What was shipped

`harness/classifier.py` (+12/-1): `RunState.observe()` now (a) matches the CLI's real
retry frame shape (`type:"system"`, `subtype:"api_retry"`, category in `error`) in
addition to the never-emitted flat `type:"api_retry"`, and (b) unwraps `rate_limit_info`
one level before reading `status`/`resetsAt` on `rate_limit_event` frames. Plus 6 new
unit tests using verbatim captured frames, the RCA doc, and a replay probe script.

## Claim-by-claim check

1. **Pre-patch classifier counts 0 retries across all captures; patched counts 10** — ran
   `python3 reports/api_retries_zero_rca_probe.py /home/lumen/foreman/_runs/wq1814/classifier.py.pre-wq1814.bak harness/classifier.py`
   directly: `[pre.bak] total api_retries counted: 0` / `[harness/classifier.py] total
   api_retries counted: 10`, all 10 in `rabbitMisty3-e8dfb9.jsonl`, verdict stays CRASH
   (certificate error, not a limit). Matches the report exactly.

2. **47 raw captures, CLI versions 2.1.233 (45)/2.1.220 (6), 74 nested `rate_limit_event`
   frames (67 `allowed_warning`, 7 `allowed`), zero `rejected` ever captured** — probe's
   own census line reproduces this verbatim. The report's stated per-directory file
   split (44+3) doesn't match current disk state (39+8), but the *total* (47) and every
   substantive number derived from it match — the split drifted because more runs
   accumulated in `foreman/_runs/fbuild-*.jsonl` after the report was written, not
   because a number was invented.

3. **New unit tests are real regression guards, not tautological** — read the diff
   (`harness/tests/test_classifier.py`): all 6 new tests build frames from
   `CAPTURED_API_RETRY` / `CAPTURED_RATE_LIMIT_EVENT`, dicts copied verbatim from the
   cited capture files, not invented shapes. Ran `python3 -m pytest
   harness/tests/test_classifier.py -q`: 50 passed (matches report's "44 before, 50
   after").

4. **Full `harness/tests` suite: 12 pre-existing failures, unrelated to this change** —
   ran the full suite: `12 failed, 843 passed in 303.97s`. Count matches. Checked all 12
   failing test files (`test_fworker_integration.py`, `test_token_injection.py`,
   `test_wq1498_dead_worker_release.py`, `test_wq1539_release_dead_worker_order.py`) for
   any reference to `classifier` — none (`grep -l classifier` on all four: empty). Their
   failure content (live `wq` binary patch checks, OAuth token file reads, real-spawn
   integration) is infra/environment-bound and has nothing to do with frame parsing.

5. **E.2 re-score: 0 of 53 re-scorable FALSE_COMPLETE attempts were CLI-error-text, 62
   unscoreable** — ran `python3 reports/wq1799_rescore_false_complete_api_errors.py`
   directly: `115` FALSE_COMPLETE token-pool attempts, `62` unscoreable, `53` re-scorable,
   `0` re-scored as API-error banner, `53` confirmed real failures. Exact match to the
   report's numbers.

6. **Adversarial edge-case probing (not in the original report, added here)** — wrote a
   scratch script (deleted after use, not committed) exercising cases the tests don't
   cover directly:
   - Two consecutive `system`/`api_retry` frames → `api_retries == 2` (no double-count
     from the `or` branch matching twice).
   - Legacy flat `{"type":"api_retry","category":"rate_limit"}` still counts and still
     sets `saw_limit` (backward compat with old fixtures, as claimed).
   - `rate_limit_event` with an empty `rate_limit_info: {}` → `saw_limit` stays `False`,
     no crash (the `rle.get("rate_limit_info") or rle` fallback doesn't misfire into
     reading the wrong dict).
   - Legacy flat `rate_limit_event` (`status`/`resetsAt` at top level, no nested
     `rate_limit_info`) still sets `saw_limit=True` and reads `resetsAt` correctly.
   All four behaved as expected — no regression, no crash, no double-count.

## Not re-litigated

- wq#1819 (fworker.py:1317 + governor `_parse_iso` epoch-int crash) is explicitly out of
  scope for wq#1814's diff and is filed separately; not this ticket's concern.
- The "isUsingOverage 529/overloaded is not LIMIT" scope call (RCA §4) is a
  no-op-by-design choice the RCA itself flags as deliberately unchanged; verified the
  code indeed never treated `overloaded` as LIMIT before or after this patch, so it's not
  a regression, just an explicit non-fix.
- ISSUE-LOG row (RCA §7) is still pending append (worker guard on `wq1814`'s attempt
  refused `/home/lumen/kb` writes); this session has `kb` in its add-dirs but appending it
  is outside this ticket's verify scope.

## Reproduce

```
python3 reports/api_retries_zero_rca_probe.py /home/lumen/foreman/_runs/wq1814/classifier.py.pre-wq1814.bak harness/classifier.py
python3 -m pytest harness/tests/test_classifier.py -q
python3 -m pytest harness/tests -q
python3 reports/wq1799_rescore_false_complete_api_errors.py
```
