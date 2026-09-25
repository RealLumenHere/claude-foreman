# wq#2723 — verify-pass: wq#2706 [rotation] burned credential sha256:e69bbe113521 (aws)

**Verdict: CONTRADICT** — wq#2706's PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1079,
merged as `fd1e0c3`) is correct on the merits (no live AWS credential, nothing to rotate) but
ships a regression test, `test_wq2702_corpus_burned_nothing`
(`harness/tests/test_mask_replay.py:1793`), that **fails on main right now**. The test's own
premise — that popping its new fixture file out of the tracked set brings back exactly its own
pinned values as `BURNED?` — is false, because a sibling ticket's PR (wq#2705, `d38a94d`,
merged one commit earlier on the same branch) already pinned an overlapping literal that
independently accounts for the same substrings.

## What was verified and holds up

- `python3 harness/mask_replay.py 2702` on current main exits 0: 4 credential-shaped values in
  wq#2702's corpus, 0 unaccounted, all marked `[tracked]`.
- The row's own hash reproduces exactly: `aws len=16 sha256=e69bbe113521 prefix='AKIA'`,
  matching the ticket title.
- The value is genuinely synthetic: it is the 16×`Q` body of wq#2702's own
  `AKIA$(printf 'Q%.0s' {1..16})` probe, embedded as a substring inside a committed 60-char
  glue fixture. `harness/masking.py:297`'s `_CANARY = "AKIA" + "Q" * 16` is the same shape by
  design (a tripwire that must always still match today's AWS pattern), which rules out
  "rule-fixed" as the accounting path here — it's accounted because the literal text is
  committed, not because the pattern stopped matching it.
- No-rotation is the right call. wq#2706's bottom line stands.

## What contradicts, with evidence

```
$ python3 -m pytest harness/tests/test_mask_replay.py::test_wq2702_corpus_burned_nothing -v
...
    rel = os.path.relpath(WQ2702_FIXTURES, mask_replay.REPO_ROOT)
    tracked.pop(rel)
    loose = mask_replay.unaccounted(
        mask_replay.replay(2702, tracked=tracked, items=items, rotated={}))
>   assert sorted(r["value_sha256_12"] for r in loose) == sorted(v[0] for v in values)
E   AssertionError: assert [] == ['6d4c27f66a96']
E     Right contains one more item: '6d4c27f66a96'
FAILED harness/tests/test_mask_replay.py::test_wq2702_corpus_burned_nothing
1 failed in 1.57s
```

Reproduced in isolation (not test-order pollution) and as part of the full file (`5 failed,
72 passed`; the other 4 — `test_the_live_rules_would_not_have_filed_wq2307`,
`..._wq2336`, `test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch`,
`test_wq2687_corpus_burned_nothing` — belong to unrelated tickets and are out of this
verify-pass's scope; not investigated further here).

The test (`harness/tests/test_mask_replay.py:1808-1812`) pops
`harness/tests/fixtures/wq2702_values.py` (added by this PR) out of `tracked` and asserts the
only value that becomes unaccounted is that fixture's own pinned github hash
(`6d4c27f66a96`). In fact **nothing** becomes unaccounted, because
`harness/tests/test_masking.py:364` already has:

```python
WQ2702_TRIPLE_GLUE = "gh" "p_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8AKIA" "QQQQQQQQQQQQQQQQxo" "xb-12345678901"
```

That literal, once concatenated, reconstructs the *exact same* 60-char string the new fixture
pins (confirmed: `grep -rln QQQQQQQQQQQQQQQQ harness` only hits these two files), and it
independently accounts for both the github value and, as a substring, the aws value
(`account()`'s primary path is a plain `text.find(value)`, `harness/mask_replay.py:436-439`).
Popping only the new fixture file leaves this second pin in `tracked`, so the test's expected
`BURNED?` never reappears.

## Root cause

A race between two sibling tickets working the same wq#2702 mask-hits corpus:

- wq#2705 (slack row, sha `254aa248acb4`) — PR #1078, commit `d38a94d`
  (`test(mask): pin the triple-glue verifier probe as a fixture`), landed on main
  2026-09-25 19:31:38Z, added `WQ2702_TRIPLE_GLUE` to `test_masking.py`.
- wq#2706 (this ticket's subject, aws row) — PR #1079's branch tip `36b54cb` was authored
  2026-09-25 19:31:27Z (11s *before* `d38a94d` landed), adding its own, redundant
  `harness/tests/fixtures/wq2702_values.py` + `test_wq2702_corpus_burned_nothing`. The PR
  was merged as `fd1e0c3` at 19:33:53Z — *after* `d38a94d` was already on main — without the
  branch being rebased to notice the now-overlapping pin, so the new test's "removing my
  fixture uncovers exactly my values" assumption was invalidated before it ever landed green.

Both individual pins are legitimate (same underlying doctrine: commit the synthetic probe
text so `mask_replay.account()` can find it). The bug is that wq#2706's test hard-codes an
exclusivity assumption about its own fixture that a concurrent, equally-valid PR broke.

## Scope note

Verify-pass, not re-implementation: no fix applied here. This report and an ISSUE-LOG row are
the deliverable. The straightforward fix (not made under this ticket) is either to delete the
redundant `harness/tests/fixtures/wq2702_values.py` / retarget
`test_wq2702_corpus_burned_nothing` at whatever `WQ2702_TRIPLE_GLUE` already proves, or to pop
*both* tracked sources before asserting. Escalating to opus-5@high per ticket instructions
(contradiction of wq#2706's shipped test).
