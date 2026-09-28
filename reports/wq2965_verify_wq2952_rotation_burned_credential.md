# wq#2965 — verify pass on wq#2952 (rotation row aws,slack set `7005e910969d`)

**Verdict: CONFIRM.** wq#2952's fix is correct and already landed on `main`.

## What wq#2952 claimed

`mask_replay.py 2922` exited 1 with 2 of 6 candidate values unaccounted, both
cloudflare-shaped (len 6, bare `cfut` prefix): `d356ea83d848` and `807399bef03a`.
wq#2952's investigation found neither is a credential — `d356ea83d848` is the English
word "ALARM" in a wq#2922 in-progress comment (unlanded, so the tree couldn't account
it), and `807399bef03a` is a Q-filled dummy an offset probe printed back on its
`cf/off5:` line. The row's original aws/slack values were already tracked elsewhere.
Verdict: no rotation needed; pin both as committed fixture text so the replay accounts
them and the tripwire doesn't re-fire.

PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1259) adds:
- `harness/tests/fixtures/wq2922_values.py` — `WQ2922_VALUES`/`WQ2922_RECORDS`, the two
  values split at their prefix seam so reading the fixture doesn't itself trip the rule.
- `harness/tests/test_mask_replay.py` — `test_wq2922_pins_are_their_hashes_and_read_silently`
  (fixture hashes match, values read silently) and `test_wq2922_corpus_burned_nothing`
  (replays wq#2922's real run dir, asserts nothing unaccounted), both built on the
  existing `_assert_corpus_pinned` helper already used by wq#2687/wq#2702/wq#2913 etc.

## What I checked

1. **Branch state**: `origin/fix/wq2952-rotation-burned-credential-2-values-set--a1`
   (622cf3a) has one commit over `main`, identical file-for-file to `f0ed168` already on
   `origin/main` (`git merge-base --is-ancestor f0ed168 origin/main` → 0). The PR is
   already landed; this verify pass is against shipped content.
2. **Reran the original failing probe**: `python3 harness/mask_replay.py 2922` →
   exit 0, `wq#2922: 7 credential-shaped values in the run's model context, 0
   unaccounted`. Both cloudflare hashes now show `[tracked]`, accounted at
   `harness/tests/fixtures/wq2922_values.py:14` and `:15`, sourced from the same
   snapshot/transcript locations wq#2952 cited (the `ops/wq2316-mask-secrets-boundary/
   mask-secrets` snapshot and the `cf/off5:` probe line). This is the real
   `/home/lumen/foreman/_runs/wq2922` run dir on this host, not a synthetic fixture.
3. **Ran the two new tests directly**: both pass
   (`test_wq2922_pins_are_their_hashes_and_read_silently`,
   `test_wq2922_corpus_burned_nothing` — 2 passed in 21.78s).
4. **Read the implementation** the tests lean on (`mask_replay.account`,
   `masking.value_sha12`, `masking.mask_text`, `_assert_corpus_pinned`) — the assertions
   are the same non-trivial shape (hash-set containment + "nothing unaccounted" +
   "removing the fixture leaves only a subset of what it pinned") already proven out
   across wq#2687/wq#2702/wq#2913's identical pattern; nothing here is a rubber-stamp
   assertion.
5. **Adversarial check on the "already tracked" claim**: the replay confirms aws
   (`e69bbe113521`) and slack (`254aa248acb4`) account to
   `harness/tests/fixtures/wq2702_values.py:12` and `harness/tests/test_mask_replay.py:
   2038` respectively — not `test_masking.py:37` as ISSUE-LOG #939's prose says (that
   line instead accounts a third, unrelated cloudflare value). Cosmetic inaccuracy in
   the log entry's citation, not a defect in the PR: all values are `[tracked]` and the
   replay's `0 unaccounted` verdict is what the probe actually gates on.
6. **Noise check**: full suite (`test_mask_replay.py` + `test_masking.py`, 222 tests,
   259s) — 220 passed, 2 failed, both pre-existing and unrelated to this diff:
   - `test_wq2913_corpus_burned_nothing` fails exactly as ISSUE-LOG #939 says it does on
     main at 322ff41 (`9fbe965f997e` etc. no longer reproduce).
   - `test_the_mask_suite_does_not_arm_its_own_tripwire` fails on a `REAL_SECRET = "..."`
     kv-secret shape introduced by `reports/wq2941_probe_self_name_punctuation_bypass.py`
     (commit 1c16d72, wq#2941 — unrelated report file, not touched by wq#2952's diff).
   Neither touches `wq2922_values.py` or the two new wq2922 tests, both of which pass.
   Not counted against wq#2952.

## Scope

Diff is two files, additive only (new fixture module, new test functions appended to
`test_mask_replay.py`). No production code path changed — `masking.py`/`mask_replay.py`
behavior is untouched, so this fix carries no regression risk beyond the tests it adds.

## Conclusion

wq#2952's root-cause read and fix are correct: neither burned value was a credential,
the fixture pin makes the replay account both, and the original failing probe now
passes clean. No contradiction, no escalation.
