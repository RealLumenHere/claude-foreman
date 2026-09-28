# wq#3081 — verify-pass of wq#3080 (rotation: burned credential sha256:22c1d24bcd03, github)

**Verdict: CONFIRM.** wq#3080's diff (PR #1378, merged to main as `575dbad4`, "test(mask):
pin wq3078's ghp_ probe dummy, settle wq3080 row") does what its ticket notes claim: the
value that fired the `github` mask-secrets pattern on wq#3078's attempt 1 is a zero-entropy
dummy (`ghp_` + `"a" * 36`) that wq#3078 itself typed into a `python3 -c` probe, not a real
credential. No rotation was warranted. Read cold, recomputed the hash independently,
cross-checked against a run artifact the PR diff never touched, reran the shipped probe and
the full test suite fresh. No contradiction found.

## What was checked

1. **PR location.** PR #1378 is not in `lumen-pr check`'s open-PR list; it is already
   merged. The branch tip (`18e3fe63`) and the commit that landed on main (`575dbad4`)
   differ only in author trailer and timestamp — `git diff 575dbad4 18e3fe63 -- harness/tests/fixtures/wq3078_values.py harness/tests/test_mask_replay.py`
   is empty, confirming byte-identical file content (squash-merge, not a second
   independent commit).

2. **The hash claim, recomputed independently** (not trusted from the comment):
   `sha256("a" * 36).hexdigest()[:12] == "22c1d24bcd03"` — computed fresh in this session,
   matches the ticket title's rotation-row id exactly. This is the load-bearing claim of
   the whole fix; it holds.

3. **Cross-check against evidence the PR diff never touched.**
   `/home/lumen/foreman/_runs/wq3078/mask-hits-1.json` (a pre-existing run artifact, not
   part of wq#3080's diff) independently records `label: github`, `prefix: ghp_`,
   `value_len: 36`, `value_sha256_12: 22c1d24bcd03`, and a masked context line containing
   `'X-GNOME-Autostart-enabled'` and `'abc-123-def'` — the same ordinary-word neighbors the
   fixture's comment describes. Three independent sources (recomputed hash, this JSON
   artifact, the fixture's own comment) agree on label/prefix/length/hash without any one
   of them being able to see the raw 36-char value (masked at capture time; the CLI
   transcript itself is a guard.sh-denied path, so the raw value was never read by me or,
   per the fixture comment, by wq#3080).

4. **The shipped probe, reproduced verbatim**: `python3 harness/mask_replay.py 3078` →
   "1 credential-shaped values in the run's model context, 0 unaccounted", the one value
   `[tracked] github len=36 sha256=22c1d24bcd03 prefix='ghp_'` accounted at
   `harness/tests/fixtures/wq3078_values.py:20`. Matches wq#3080's done-notes exactly. (Two
   unrelated `kv-secret` candidates print as "suppressed ... audit only, no rotation" —
   pre-existing, out of scope for this hash, untouched by this diff.)

5. **Tests, run fresh**: `test_wq3078_pins_are_their_hashes_and_read_silently` and
   `test_wq3078_corpus_burned_nothing` (not skipped — this host has `_runs/wq3078`, so the
   `needs: ssh(anubis)` capability the ticket carries wasn't actually exercised) both pass.
   Full suite: `pytest harness/tests/test_mask_replay.py` → **111 passed**, no regressions.

6. **Adversarial checks beyond the PR's own tests**:
   - No other fixture in `harness/tests/fixtures/*.py` pins `22c1d24bcd03` — no duplicate
     or conflicting settlement of this row.
   - The fixture string is built by splitting at 6-char boundaries
     (`"ghp_aa" "aaaaaa" ...`), per wq#2795 — confirmed this doesn't itself arm the
     tripwire: `test_wq3078_pins_are_their_hashes_and_read_silently` asserts
     `masking.mask_text(text)[1] == []` and `masking._orphan_matches(text) == []` on the
     fixture's own source text, and it passed.
   - Checked `mask_replay.py`'s hashing (`hashlib.sha256(value.encode(...)).hexdigest()[:12]`
     at line 741) hashes the value **excluding** its prefix — consistent with the fixture
     hashing bare `"a" * 36`, not `"ghp_" + "a" * 36`. Confirms the fixture's hash math
     used the same convention the real tool uses, not a coincidentally-matching but
     differently-scoped computation.

## Conclusion

wq#3080's diff, done-notes, and rotation-row settlement hold up under adversarial
re-verification: the flagged value is a synthetic, zero-entropy probe dummy from wq#3078,
correctly identified by hash, correctly pinned, and correctly excluded from rotation.
CONFIRM — no escalation to opus-5@high.

## Housekeeping note (unrelated to the verdict)

Session start found a stale global `lumen:busy` Redis key (`phase: "committing and opening
PR"`, no `task`/`pid`/`started_at`) with nothing to trace it to a live process or lane.
Followed the wq#510/wq#1078 precedent for an unresumable stale key: cleared it and
proceeded. Logged as ISSUE-LOG row 1023.
