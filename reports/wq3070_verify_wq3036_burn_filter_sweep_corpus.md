# wq#3070 [verify] wq#3036 — burn_filter's tree-wide sweep corpus is empty

**Verdict: CONFIRM.** wq#3036's PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1359,
merged as `051825c1` = `df8ef8e2` + `ec7f55b8`) does what it claims. No contradiction
found; no escalation to judgment.

## What I checked

Read the diff cold: `harness/tests/fixtures/wq3036_sweep_corpus.py` (new, 25 lines) and
the `TRIPWIRE_CORPUS_EXCUSED` addition in `harness/tests/test_masking.py`. Then traced the
regex mechanics behind the claim by hand instead of trusting the docstring, and ran the
evidence.

### 1. Reproduced the bug pre-fix

Checked out the merge's first parent (`6e77af49`, pre-wq#3036 main tip) in a scratch
worktree and ran the target test:

```
$ pytest .../test_burn_filter.py::test_no_tracked_module_pages_when_a_worker_reads_it -v
FAILED — AssertionError: sweep is vacuous: 0 files match a pattern, none page unaccounted
```

Confirms the ticket's premise: `test_no_tracked_module_pages_when_a_worker_reads_it`'s
anti-vacuity half (wq#2630) genuinely fails on the pre-fix tree — this isn't a
already-fixed or unreproducible bug.

### 2. Probe passes post-fix

Same test against the current worktree (`051825c1`, HEAD):

```
$ pytest harness/tests/test_burn_filter.py::test_no_tracked_module_pages_when_a_worker_reads_it -v
PASSED
```

Full targeted + suite run:

```
$ pytest harness/tests/test_burn_filter.py harness/tests/test_masking.py -q
1 failed, 177 passed, 1 skipped
```

The one failure is `test_masking.py::test_the_mask_suite_does_not_arm_its_own_tripwire`,
on two files **untouched by this PR** — `reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md`
and `reports/wq3062_judgment_wq3061_rotation_accounted_in_false_positive.md`. Both predate
the wq#3036 merge (`git merge-base --is-ancestor` confirms both their introducing commits,
`51045dd8` and `0900be93`, are ancestors of `051825c1`), and `TRIPWIRE_CORPUS_EXCUSED`
never covered either file, before or after this diff. Pre-existing and already logged —
ISSUE-LOG row 1004 names the `wq3021` half of it as "open: independent of any branch";
the `wq3062` half is separately logged at row 1012. Unrelated to this PR's scope.

### 3. Why the fixture actually produces a match (not just a docstring claim)

The new line is `pending_rotation_secret = queued_epoch_signing_material` — a bare
identifier value, deliberately not a string literal. Traced why this isn't suppressed the
same way the original corpus (`key = apply_key_allowlist(reg)` and kin) was:

- `_KV_NOT_CALLED` (masking.py:252) only blocks values immediately followed by `(` —
  irrelevant here, nothing calls anything.
- `_KV_NOT_IDENT` (masking.py:283) is `(?(kn)...)`, a **conditional** on group `kn`, and
  `kn` is only set when the label noun is literally `key` (masking.py:157:
  `(?:secret|token|(?P<kn>key)|...)`). The fixture's label is `secret`, not `key`, so `kn`
  is unset and the conditional is a no-op — the bare-identifier suppression that gutted
  the original corpus (wq#2288/wq#2297, `key`-labeled) does not apply to a
  `secret`-labeled bare identifier. This is why the fixture picks `secret` over `key`:
  it's a deliberate, correct dodge of the exact guard that emptied the corpus, not an
  accident.
- No self-name suppression (`_KV_NOT_SELF`) applies — the value doesn't repeat the label.

So `masking.mask_text()` genuinely labels this line `kv-secret`, matching the PR's claimed
shape.

### 4. Length/hash claim checks out

The fixture docstring and the `TRIPWIRE_CORPUS_EXCUSED` row both cite
`value_sha256_12 bf43137fb255`, shape `(kv-secret, "pending_rotation_secret = ", 29)`.
Recomputed independently:

```
$ python3 -c "import hashlib; v='queued_epoch_signing_material'; print(len(v), hashlib.sha256(v.encode()).hexdigest()[:12])"
29 bf43137fb255
```

Matches exactly.

### 5. Provenance: is_committed_literal finds it in itself

`burn_filter.is_committed_literal` (burn_filter.py:179) runs `git grep --fixed-strings -e
<value> -- .` over the repo root with no self-exclusion. Since the exact identifier text
is committed verbatim in this same tracked fixture file, real `git grep` finds it —
confirmed by the passing test in §2 using the real `subprocess.run` runner, not a stub.

## What I tried to break it with

- Ran the target test at the pre-fix commit to make sure the "fix" isn't a no-op against
  an already-passing test (it wasn't — it genuinely failed pre-fix, §1).
- Checked whether the fixture's bare-identifier value would fall into the same
  `_KV_NOT_IDENT` trap that killed the original corpus; traced the `kn`-group conditional
  by hand to confirm `secret` labels are exempt (§3), rather than trusting the docstring's
  assertion that it would match.
- Checked whether the one test failure in the full suite run was caused by this PR;
  confirmed by ancestry (`merge-base --is-ancestor`) that both offending files predate the
  merge and are absent from `TRIPWIRE_CORPUS_EXCUSED` both before and after — not a
  regression this PR introduced, and already tracked in ISSUE-LOG (rows 1004, 1012).
- Recomputed the sha256[:12]/length claims independently rather than trusting the
  docstring's numbers (§4).

Found nothing the PR fails to disclose and no construction that breaks a claim it
actually makes.

## Conclusion

CONFIRM. The bug reproduces pre-fix, the fix resolves it for the real (non-stubbed)
runner, the anti-vacuity half still correctly fails under `FakeGrep`, the fixture's
label choice is a deliberate and correct dodge of the exact suppression that caused the
original vacuity, and the one adjacent test failure is pre-existing, unrelated, and
already logged. No escalation warranted.
