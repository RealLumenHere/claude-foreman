# wq#2984 verify-pass: wq#2955 ([judgment] wq#2946 contradicted wq#2936: wq2913 rotation pin)

**Verdict: CONFIRM.** wq#2955's fix (PR#1268, `d2bd4b50`, merged `de077d637`, on `main`)
is correct, falsifiable, and does not regress the surrounding suite.

## What was checked

wq#2955 rewrote `harness/tests/fixtures/wq2913_values.py` and
`harness/tests/test_mask_replay.py` so the wq#2913 rotation-pin tests stop asserting a
hardcoded count (`len(WQ2913_VALUES) == 8`) against a fixture that only ever held 7
tuples, and instead assert *coverage*: every one of ISSUE-LOG row 924's 8 historical
hashes must be pinned by one of two disjoint sets — `WQ2913_VALUES` (what today's rule
table still cuts) or `WQ2913_RULE_RETIRED` (what it no longer cuts, asserted by hash via
`masking.scan`, not by an empty scan). This was the JUDGE instruction from wq#2955's own
ticket body (add the missing 8th tuple; re-pin or drop the url-auth pin), generalized to
all 7 retired values rather than just the one url-auth pin — a broader but consistent
application of the same instruction.

1. **Merge check.** `git merge-base --is-ancestor d2bd4b50 main` — confirmed `d2bd4b50`
   is on current `main` (HEAD `5466acac` at verify time).
2. **Green as shipped.** `pytest harness/tests/test_mask_replay.py -k wq2913` on merged
   `main`: 3 passed (`test_wq2913_pins_are_their_hashes_and_read_silently`,
   `test_wq2913_retired_pins_are_not_cut_by_todays_table`,
   `test_wq2913_corpus_burned_nothing` — the last is NOT skipped; `_runs/wq2913` exists
   on this host). Adding the adjacent `test_a_failing_pin_names_hashes_not_values`
   (also touched by this diff) makes 4 passed, matching wq#2955's closing notes.
3. **Falsifiability probe (wq#2630).** Reverted both files to their pre-fix blobs
   (`d2bd4b50~1`) in this worktree only and re-ran `-k wq2913`:
   `test_wq2913_corpus_burned_nothing` FAILS with the exact missing-hash set wq#2946
   documented (`72b0b008eebb, 810f9fde938b, 9fbe965f997e, b4ef69356f53, ba8aad54215e,
   cb01c565429c, f3a0c6e016c9`). Restored to `main`'s versions afterward
   (`git checkout main -- <files>`); worktree is clean. This proves the pin is not
   vacuous — it demonstrably can fail, and did, on the code it's meant to catch.
4. **Direct mechanism check.** Independently reproduced the retirement claim against
   `masking.scan()` outside the test harness: `scan("sk-key-letter-xoxb--off1-masking")`
   returns only `{'567fd51d9d8a'}` (label `slack`, prefix `xoxb-`) — the retired sk-key
   hash `ba8aad54215e` does not appear, because the slack rule now claims the span first
   (960404a, "mask the union of every rule's spans"). Matches the fixture's own comment
   and the `WQ2913_VALUES[0]` tuple exactly.
5. **Collateral-damage check.** Full `pytest harness/tests/test_mask_replay.py`: 93
   passed, 1 failed. The one failure — `test_wq2922_corpus_burned_nothing` — is
   unrelated to wq#2913/wq#2955 (a different fixture, wq#2922's), already filed and
   claimed as wq#2982 ("wq#2972's probe split orphaned a kv-secret snapshot in wq2922's
   corpus"), and explicitly called out as out-of-scope in wq#2955's own closing notes.
   Not caused by this diff; not this ticket's concern.

## Minor discrepancy (non-blocking)

None found beyond what's already noted above — the "4 passed" in wq#2955's closing
notes checks out once the adjacent pin-naming test is included in the `-k` selection.

## Scope note

wq#2955 explicitly did not touch (and this verify-pass did not re-open): wq#2981
(own-tripwire test red on main) and wq#2982 (test_wq2922_corpus_burned_nothing, observed
directly above, already claimed by another lane). Both are separately tracked.
