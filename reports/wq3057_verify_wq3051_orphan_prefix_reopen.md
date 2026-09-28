# wq#3057 [verify] wq#3051 — orphan backstop's trailing run must not re-open on a prefix

**Verdict: CONFIRM.** wq#3051's PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1339,
merged `a311cc2719` / `391a11b8`) does what it claims. No contradiction found; no
escalation to judgment.

## What I checked

Read the diff cold (`harness/masking.py`, mirrored `token-engine/token_engine_daemon.py`,
`harness/tests/test_mask_underfloor_invariant.py`, the probe, the judgment report). Traced
the regex mechanics of the third condition by hand rather than trusting the docstring's
argument, then ran the evidence.

### 1. Probe reproduces cleanly on current `main`

```
$ python3 reports/wq3051_probe_orphan_prefix_reopen.py
1 reopen: no-credential text must not alarm: 20 cases, 0 failing
2 under-floor via MASK guard (SILENT = fail): 96 cases, 0 failing
5 second link of the chain still fires and is redacted: 6 cases, 0 failing
3a reopen [daemon mirror]: 20 cases, 0 failing
3b under-floor [daemon mirror] (SILENT = fail): 96 cases, 0 failing
4 quiet on committed text outside wq#3018's sweep @fe123068: 7 cases, 0 failing
TOTAL FAILING 0
```

Matches the PR's own before/after table exactly (20/20 alarming pre-fix per the report,
0/0 now). Section 1's corpus is built outside wq#3018's population (wq#2630), asserts
`mask_text()` returns input byte-for-byte first, and covers cross-family reopeners too.
Section 2/3b (wq#2868 section B, the 96 real under-floor leaks) still 0 SILENT — the
tightening did not reopen wq#2922. Section 5 falsifies the fix's own argument if wrong
(a full-length second link would go silent or unredacted if the "already read as its own
match" reasoning were false) — it doesn't.

### 2. Targeted + full test suite

```
$ pytest tests/test_mask_underfloor_invariant.py tests/test_mask_mirror_coverage.py -q
61 passed
$ pytest tests/test_mask_*.py -q   (7 files)
1 failed, 1250 passed in 403.32s
```

The one failure is `test_masking.py::test_the_mask_suite_does_not_arm_its_own_tripwire`
on `reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md`, exactly the pre-existing,
unrelated failure the PR's own report discloses and logs as ISSUE-LOG row 1003 for a
separate ticket — confirmed identical before and after this diff. Pass count (1250 vs the
report's 1243) is fully explained by wq#3048 (`4b4e69f7`, three commits later on `main`)
adding cases to `test_mask_replay.py`; not a discrepancy in this PR.

### 3. Manual regex trace of the third condition

Worked through `_orphan_spans()`'s new guard —
`if _PRE_ANY.match(text, tail.end()): continue` — by hand against
`"x cfut_QQQQMASKEDcfut_yy"`: confirmed `tail.end()` always sits immediately after the
literal `MASKED` (regardless of how many prefixes `_OWN_TAIL` absorbed in front of it),
so `_AFTER_MASK` is unconditionally satisfied there, which means `_B` is satisfied there
for ANY orphan rule — so a real prefix starting at that offset is *guaranteed* to spawn
its own independent match in the same `finditer` loop (traced: `finditer` resumes
scanning from the first match's `m.end()`, which is *before* the literal since `_body()`
stops there, so nothing is pre-consumed). The "already read as its own match" claim in
the docstring and judgment report holds up under trace, not just assertion.

### 4. Span/redaction claim

Confirmed in `mask_text()` (masking.py:973-982): `_orphan_matches()` only ever appends to
`hits`, never touches `spans` (which come solely from `read()`). The "never a leak either
way — `e2dbe416` stays on main" claim is structurally true; this PR only changes whether
an *alarm* fires, never what gets redacted.

### 5. Daemon mirror

Byte-identical third-condition logic in `token_engine_daemon.py:301-330`, same
`_PRE_ANY`/`_OWN_TAIL`/prefix definitions as `masking.py`. No drift between copies.

## What I tried to break it with

The disclosed residual gap — a genuinely split live secret whose post-`MASKED`
continuation coincidentally spells one of the six prefixes, which would now be silently
dropped instead of alarmed — is exactly the kind of outside-population case archetype 8
calls for. I did the probability check by hand for the shortest, most permissive case
(`sk-` inside a charset that admits lowercase + dash): joint probability of the literal
`MASKED` coincidence (~62⁻⁶) *and* an immediately-following `sk-` coincidence (~64⁻³) is
their product, strictly rarer than either alone — the report's "rarer than the 1-in-62**6
the literal already costs" claim checks out numerically, not just rhetorically. This gap
is not new: the PR's own docstring (masking.py:712-725) and judgment report (§"What it
gives up") already name it and accept it as a priced trade-off, the same class of call
wq#3018 itself deferred. I did not find anything the PR failed to disclose, and no
construction that breaks a claim it actually makes.

## Conclusion

CONFIRM. Evidence reproduces, the position-anchored argument is sound under trace, the
daemon mirror matches, and the one test failure is pre-existing/unrelated and already
logged. No escalation warranted.
