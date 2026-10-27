# wq#4738 — verify wq#4736 [rotation] burned credential sha256:09c067d6e975 (kv-secret)

Verdict: **CONFIRM**

## What wq#4736 shipped (PR #2528, dd7f4d07)

`harness/fworker.py::mask_alarm` now loads `mask_replay.load_rotated_ledger()` and, if
every hash in the current hit is already a key in that ledger, logs and returns instead
of paging/filing a pri-1 row. All-or-nothing, mirroring the pre-existing self-output skip
immediately above it and the pattern `mask_at_rest.py` already used (line 498 there).
Two new tests cover the skip and the all-or-nothing boundary (a settled hash sharing a
line with a live one must still alarm). `harness/tests/test_masking.py` also had to
monkeypatch `fworker.mask_replay.load_rotated_ledger` in the pre-existing
`test_mask_alarm_pages_and_files_rotation_ticket`, since it would otherwise now hit the
real on-disk ledger.

## Checks performed

1. **Premise**: confirmed `rotated_ledger.json` already carries
   `09c067d6e975 -> {ticket: wq3189, note: "NOT ROTATED - not ours... gitlab.com... public
   Sentry browser DSN... Public by design, unrotatable by us"}`, dated 2026-09-29, i.e.
   genuinely pre-existing and not something wq#4736 added for itself.
2. **Replay claim**: re-ran `python3 harness/mask_replay.py 4734` myself (not trusting the
   done-notes figure) — `0 unaccounted`; the one recorded hit (`kv-secret sha256=09c067d6e975`)
   is flagged "since-narrowed rule... audit only", matching the PR's claim that nothing new
   needed rotating.
3. **Code reading**: `mask_alarm`'s new block sits after the self-output skip and before
   `rotation_chain_refire`/paging — can't suppress a page for a hash set containing even
   one unsettled value, since `all(...)` on a mixed set is correctly False. Confirmed
   `burned_hashes` returns `[]` (falsy) when there's nothing to key on, so an empty-hash
   alarm can't vacuously match `hashes and all(...)` and get swallowed by this branch.
4. **Comment-vs-code claim**: the docstring asserts "mask_at_rest skips it the same way" —
   verified `mask_at_rest.py:498` independently calls `mask_replay.load_rotated_ledger()`
   and already had this behavior before wq#4736, so the new code is bringing `mask_alarm`
   into line with an existing sibling path, not inventing an unverified claim.
5. **Tests**:
   - `pytest harness/tests/test_masking.py -k "rotated_ledger or unsettled_value or
     mask_alarm_pages"` — 3 passed.
   - Full `harness/tests/test_masking.py` — 190 passed, 2 failed
     (`test_the_mask_suite_does_not_arm_its_own_tripwire`,
     `test_doctrine_corpus_does_not_re_arm_the_alarm`). Both scan live, unpinned content
     (`kb/ISSUE-LOG.md`, `reports/*.md`) that drifts as other tickets append rows; neither
     test touches `mask_replay`/ledger code, and `git diff bbf45955 dd7f4d07 --stat` shows
     this change never touched `masking.py`. The ISSUE-LOG row the author filed for this
     ticket (row 1617) *already discloses* "Pre-existing on base bbf45955 (not this
     change): 4 corpus tests fail on committed reports/ and limpet csv files" — count has
     since moved (2 today vs. 4 then) consistent with ongoing corpus drift, not a fix
     regression.
   - All 5 `test_fworker_*.py` files (52 tests) — pass, confirming the new
     `import mask_replay` at module load doesn't break anything else in fworker.py.

## Conclusion

The diff does what the ticket and done-notes claim: it closes the exact re-fire path
wq#4736 was filed against (a ledgered, dead/non-credential value re-triggering a fresh
pri-1 row), it's scoped tightly (all-or-nothing, placed correctly in the alarm's control
flow), it's tested both for the happy path and the adversarial mixed-set case, and the
two test failures present in the full suite run are pre-existing corpus drift unrelated
to this change — already disclosed by the author in ISSUE-LOG row 1617. No contradiction
found; no escalation warranted.
