# wq#3193 — verify wq#3174 (issue-log-hygiene: 15 new findings vs baseline)

**Verdict: CONFIRMED.** wq#3174's fix (PR#1455, commit `1ae93c55`) resolves the
collision. The live hygiene alarm, which was firing 15 new findings at 21:37Z 09-29,
now reports zero new and zero resolved findings against the pinned baseline — an
exact match. The flock allocator is genuinely load-bearing (reproduced the collision
with it removed), the test suite runs real concurrent subprocesses rather than mocks,
and all 15 (not just the 9 named in the ticket text) fresh duplicate rows were
correctly disambiguated per the wq#2857 suffix policy, with citations rewritten only
where they meant the renamed row.

## What wq#3174 shipped

- `harness/issue_log_append.py`: allocates the next ISSUE-LOG row number under an
  exclusive `flock` held for the whole read-max-append sequence, replacing the
  read-then-Edit "max + 1" pattern that let two workers pick the same number.
- `harness/prompts/worker.md`: doctrine now points workers at the script instead of
  an Edit-based append (confirmed live — this exact text is in my own system prompt
  for this session).
- `kb/ISSUE-LOG.md`: 15 collision rows (915, 920, 926, 984, 989, 995, 996, 1022, 1025,
  1031, 1050, 1055, 1062, 1064, 1075) each got a `b`-suffixed second occurrence per the
  wq#2857 disambiguation policy (`reports/2857_policy_issue_log_row_disambiguation.md`);
  row 1012 got a back-reference to row 1017, closing the one-way pointer.
- 3 reports (`wq2923_verify_wq2868_foreign_prefix.md`,
  `wq3022_verify_wq2971_drills575_live_claim.md`,
  `wq3134_verify_wq3133_kv_secret_echo_guard_bound.md`) had their citations of the
  renamed rows (`920`→`920b`, `984`→`984b`) rewritten.
- `harness/tests/test_wq3174_issue_log_append.py`: 5 tests, fixture-only.

## Verification performed

1. **Reproduced the probe verbatim.**
   `python3 ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py` against the
   live `/home/lumen/kb/ISSUE-LOG.md`: `baseline ... 131 known, live findings: 131`,
   **no** `NEW`/`RESOLVED` lines printed, `OK (no new findings beyond baseline)`. The
   live finding set is identical to the pre-collision baseline — the exact failure
   mode this ticket exists to catch is gone, and nothing else shifted.

2. **Ran the shipped tests.** `test_wq3174_issue_log_append.py` (5 passed) and
   `test_wq2857_issue_log_alarm.py` (6 passed, no regression in the alarm itself).

3. **Confirmed the probe can fail (wq#2630).** `test_append_waits_for_the_lock` and
   `test_parallel_appenders_never_collide` spawn real subprocesses, not mocks. To
   independently verify the flock is load-bearing rather than a tautological test, I
   copied `issue_log_append.py` to a scratch file with both `fcntl.flock(...)` calls
   replaced by `pass` and re-ran 12 concurrent appenders against it:
   `numbers returned: [101, 102, 103, 104, 105, 106, 106, 107, 108, 109, 110, 111]` —
   row 106 duplicated, 11 unique of 12. The same 12-way run against the real,
   lock-holding script (via pytest, above) produced no duplicates. This is a genuine
   falsifiable probe, not one that only ever passes.

4. **Checked every renamed row exists exactly as the fix claims.**
   `grep -noE "^\| [0-9]{3,4}b \|"` on the live log shows all 15 target rows
   b-suffixed (plus two pre-existing ones, 887b/889b, from wq#2894, untouched).

5. **Spot-checked citation rewrites for correctness, not just presence.** Row 984
   (bare) is wq#2924's sk-key finding; row 984b is wq#3020's drills_575.py
   cross-repo-provenance finding. `wq3022_verify_wq2971_drills575_live_claim.md`'s
   "same wall as ISSUE-LOG rows 984/985/986" was rewritten to `984b/985/986` — correct,
   since the report's subject (cross-repo provenance) is 984b's topic, not 984's.
   Same check on row 920 (bare = wq#2908 PATH-parking workaround) vs 920b (wq#2923
   collision entry): the rewritten citation in
   `wq2923_verify_wq2868_foreign_prefix.md` ("ISSUE-LOG row 920 redeploy" →
   "row 920b redeploy") is about a redeploy, which is 920b's content, not 920's.

6. **Checked for missed citations both directions.** Grepped `foreman/` and `kb/` for
   bare-number citations to all 15 affected rows: found only the four bare `row 920`
   citations in ISSUE-LOG.md itself (lines 1061/1082/1091/1099), all of which are
   genuinely about wq#2908's PATH-parking topic (920's actual content) — correctly
   left unrewritten per "citations of the first occurrence were left alone." No
   stray citation anywhere in `foreman/*.md`/`*.py` or `kb/*.md` still points at a
   bare number that should now read the `b` form.

7. **Adversarially checked the two documented edge cases** (row 1022's line-order-vs-
   blame disagreement, row 1064's line-order fallback) against real `git -C
   /home/lumen/kb blame` output, since I suspected the "blame wins" methodology might
   not actually be usable if the whole day's rows were uncommitted (kb hasn't
   auto-committed since 05:04Z 09-29 per wq#3174's own notes). Blame on lines
   1160-1170 shows row 1022 (bare, wq#3080) really is committed to `4d99c2d5`
   (2026-09-28 20:26:16), while 1022b (wq#2654) is "Not Committed Yet" — confirming
   wq#2654's entry is genuinely the later one despite sitting on the physically
   earlier line, exactly as the policy amendment claims. Blame on lines 648-654 shows
   row 1064 (bare, wq#2611) is *also* "Not Committed Yet," matching the amendment's own
   admission that blame couldn't separate this pair and line order was used instead.
   Both edge cases check out against live git history rather than just the PR's prose.

## Adversarial angles tried and ruled out

- Suspected the ticket's list of "9 duplicate row IDs" undercounted the real scope —
  confirmed the fix actually handled all 15 fresh collisions (matching the alarm's own
  "15 new findings" and the policy amendment's "15 fresh collisions (915...1075)"),
  not just the 9 named as examples in the auto-generated ticket text. Not a defect;
  the ticket text was incomplete, the fix wasn't.
- Suspected citation rewrites might have been applied mechanically (rename-all) rather
  than by actual referent, which would silently point some citations at the wrong row
  — ruled out by reading both candidate rows' content for two of the three rewritten
  citations and confirming the rewritten target matches the citing prose's subject.
- Suspected the "blame wins" edge-case handling was unfalsifiable prose with no real
  git evidence behind it — ruled out by running `git blame` myself against the live kb
  repo and getting results consistent with the policy amendment's claims.
- Suspected the flock test might pass vacuously (e.g., subprocess overhead alone
  serializes 12 quick appends even without a real lock) — ruled out by reproducing the
  collision on a flock-removed variant of the exact same script under the exact same
  12-parallel-process load.

## Conclusion

wq#3174 ships a correct, tested fix: the flock allocator prevents the collision class
going forward (verified adversarially, not just by reading the diff), the 15 existing
duplicates were disambiguated per policy with citations rewritten only where the
citation's actual subject required it, and the live hygiene alarm — the ticket's own
acceptance signal — now reports zero new findings, exactly reproducing the baseline.
Confirming on the board, no escalation.
