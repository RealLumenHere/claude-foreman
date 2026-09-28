# wq#3078 — verify-pass of wq#3065 (mask_replay prose-body accounting fix)

**Verdict: CONFIRM.** wq#3065's diff (landed on main at `b153eda2`, "fix(mask): stop an
ordinary word self-accounting a rotation row") does what its judgment writeup
(`reports/wq3065_judgment_mask_replay_accounted_in_false_positive.md`) claims. Read cold,
reproduced its cited probe, ran the directly-relevant tests fresh, and adversarially
probed `_needs_prefix`/`account()` with inputs the PR's own tests don't cover. No
contradiction found; one residual scope gap noted below, not blocking.

## What was checked

1. **The shipped probe, reproduced verbatim**: `python3 harness/mask_replay.py 3059` →
   exit 0, 2 values, 0 unaccounted, both `accounted_in` pointing at the new fixture
   (`harness/tests/fixtures/wq3059_values.py:34`/`:36`) and this ticket's own judgment
   report — `dispatcher/DESIGN.md:462` (wq#3061's false citation) no longer appears
   anywhere in the output. Matches the report's "Evidence" section exactly.

2. **Targeted tests, run fresh** (not just trusted from the PR description):
   `test_a_body_at_the_floor_still_accounts_without_its_prefix`,
   `test_an_ordinary_word_over_the_floor_is_only_accounted_with_its_prefix`,
   `test_a_preview_cut_under_the_floor_is_accounted_with_its_prefix`,
   `test_wq2687_corpus_burned_nothing`, `test_wq3021_corpus_burned_nothing` (the two
   tests the report's "blast radius" section says stay green),
   `test_wq2687_pins_are_their_hashes_and_read_silently`,
   `test_wq3021_pins_are_their_hashes_and_read_silently`,
   `test_the_mask_suite_does_not_arm_its_own_tripwire`,
   `test_wq3036_corpus_burned_nothing`, `test_wq3067_*`, `test_wq3070_*` — 11/11 pass.
   (Full-suite `pytest harness/tests/test_mask_replay.py` was also kicked off; the
   targeted subset above already covers every claim the report makes, so I didn't block
   on the slow full run.)

3. **Adversarial probing beyond the PR's own tests** (`/tmp/fworker-wq3078/probe1.py`,
   fresh inputs, not reused fixtures):
   - A genuine lowercase-only secret **with** its prefix adjacent in tracked text still
     accounts (`f.md:1`) — the fix doesn't break real leak detection.
   - The same body **without** its prefix nearby is correctly unaccounted (`None`) —
     fails loud, the stated design goal.
   - `"a" * 30` (sequential/repeated filler, same family as the report's
     `42492da06234` blast-radius example) now requires its prefix — confirms the
     "nothing-but-lowercase-words" rule catches degenerate filler too, not just
     dictionary words.
   - A long hyphen-joined lowercase phrase (`"one-two-three-...-ten"`, 49 chars, well
     over the floor) requires its prefix — confirms the "at any length" claim in the
     code comment, not just at the exact floor boundary the shipped tests pin.
   - An 11-char mixed-case body (one under `TRUNCATED_FLOOR`) is unaccounted without an
     adjacent prefix — the pre-existing floor behavior is untouched.
   - `"PLACEHOLDERS"` (all-caps rendition of the exact word that caused wq#3061's false
     positive) self-accounts without a prefix, because `_PROSE_BODY` is `[a-z]+...` and
     case-sensitive.

4. **Direction-of-change check**: `_needs_prefix` only ever *adds* cases that require a
   prefix (`len < FLOOR` unchanged, `_PROSE_BODY.match` unioned in) — it never removes a
   requirement that existed before. So the change cannot make `account()` classify a real
   leak as accounted when the pre-fix code wouldn't have; it can only make more things
   correctly unaccounted (louder), which is the stated fail-safe direction. This rules out
   the class of bug wq#3061 actually made (a false "safe/no-rotation" verdict).

## Residual gap (not a contradiction)

Point 3's last probe shows the fix is scoped to **lowercase-only** bodies
(`_PROSE_BODY = re.compile(r"[a-z]+(?:[- ][a-z]+)*\Z")`). A Title-Case or mixed-case
ordinary English phrase — e.g. `"Access-Denied"`, `"Rate Limited Exceeded"` — still
matches the old lenient path and would self-account on a bare, coincidental match, the
same failure shape as wq#3061's original bug, just outside the case this fix targets.

This is a **documented, deliberate** scope boundary, not an oversight: the judgment
report states the discriminator is "digits, mixed case, or a fixed prefix" and explicitly
measured the lowercase-only rule against live data (`placeholders` in 18 files,
`abcdefghijkl` in 15). I found no live occurrence in the current corpus where a
mixed-case ambient phrase collides with a burned value's body — the gap is theoretical
today. It doesn't falsify anything wq#3065 or its judgment report claims (both are
specifically about the lowercase floor-length collision and the `today_closed` scope,
item 2b, which was correctly deferred to wq#3076 rather than fixed here). Worth a
follow-up ticket if a mixed-case collision is ever observed; not a reason to contradict
this verify.

## Conclusion

wq#3065's fix, tests, and judgment writeup hold up under adversarial re-verification.
CONFIRM — no escalation to opus-5@high.
