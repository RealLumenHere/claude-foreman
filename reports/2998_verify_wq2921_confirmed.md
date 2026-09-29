# wq#2998 verify of wq#2921 (PR #1286): CONFIRMED

Verifier: final-push-ad230d, 2026-09-29. Export of origin/main (fc6d70b8), not the author's branch.

- Landed as 845dfb0f (squash of ef36faeb); `git merge-base --is-ancestor 845dfb0f HEAD` true.
- `dispatcher/tests/test_capability_replay.py`: 12 passed, 1 xfailed (the non-gating live smoke, as designed).
- The gating drill (`test_replay_of_the_four_stuck_tickets_fixture`) reads only the pinned
  `fixtures/four_stuck_tickets.json` through a tmp queue.db, never the live one.
- Falsified both ways, each failed the drill as it should, then restored and green again:
  1. appended `/home/lumen/.secrets/master.env` to ticket 1401's detail -> `capability_denied_by_policy`
  2. stripped every path citation from all four fixture rows -> assertion at line 222 (no allow.read_dirs advisory)
- Residual: the any-of-four `allow.read_dirs` assertion is deliberately weaker than the old 1402-specific one
  (1402 was rewritten 2026-09-03). Acceptable: mutation 2 shows it still bites.
