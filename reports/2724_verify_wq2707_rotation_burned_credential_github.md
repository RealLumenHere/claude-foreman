# wq#2724 — verify-pass: wq#2707 [rotation] burned credential sha256:6d4c27f66a96 (github)

**Verdict: CONFIRMED, nothing rotated** — nit only (attribution, not evidence).
PR#1080 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1080) is correctly
superseded and safe to close: `main..fix/wq2707-rotation-burned-credential-sha256-6d4c27-a1`
is empty (zero commits ahead of main), so nothing is lost by not merging it.

## What was verified and holds up

- Ran the probe cold: `python3 harness/mask_replay.py 2702` on current main (`92586bf`)
  exits clean, reports the row's own hash exactly —
  `[tracked] github len=60 sha256=6d4c27f66a96 prefix='ghp_'`, `accounted:
  harness/tests/fixtures/wq2702_values.py:12`, 0 unaccounted overall.
- Read the accounted file directly rather than trusting the tool: `wq2702_values.py`
  pins `("6d4c27f66a96", "github", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8AKI"
  "AQQQQQQQQQQQQQQQQxoxb")` — the same wq#2702 triple-glue probe (github fixture +
  `AKIA`+16x`Q` + `xoxb`) wq#2707's notes describe, split at a seam so reading the file
  doesn't re-arm the alarm.
- Unlike wq#2718's finding against wq#2703 (row 813: a 10-char value under
  `mask_replay.py`'s `account()` floor-less match, a coincidental substring collision),
  this value is 60 chars — far above `TRUNCATED_FLOOR` (12) — so the same
  no-length-floor gap in `account()`'s primary literal-match path cannot produce a false
  positive here. The match is a real, specific 60-byte string equality, not a short
  substring collision.
- The branch `fix/wq2707-rotation-burned-credential-sha256-6d4c27-a1` (PR#1080) has zero
  commits ahead of main (`git log main..<branch>` is empty) — confirms "superseded,
  nothing to merge."
- ISSUE-LOG row 808 (written by wq#2707 itself) correctly credits both siblings: "The
  value is committed text now that wq#2705 and wq#2706 have landed" — accurate, since
  wq#2705 (PR#1078) pinned a *different* fixture (`WQ2702_TRIPLE_GLUE` in
  `harness/tests/test_masking.py`) and wq#2706 (PR#1079, commit `36b54cb` /
  landed on main as `fd1e0c3`) is the one that actually added
  `harness/tests/fixtures/wq2702_values.py`.

## Nit found, does not change the verdict

wq#2707's terse wq-board closing note says: "PR#1080 superseded by **wq#2705's**
identical fixture already on main (harness/tests/fixtures/wq2702_values.py:12)." That
specific attribution is wrong — `git log --oneline -- harness/tests/fixtures/wq2702_values.py`
shows exactly one commit, `fd1e0c3` ("test(mask): pin wq2702's synthetic glue probe as
committed text", `Refs: wq#2706`), and `git diff main fix/wq2705-...-a1` shows wq#2705's
branch never touched this file at all (wq#2705's own notes confirm it pinned a separate
fixture in `test_masking.py` instead). The fixture at `wq2702_values.py:12` is wq#2706's
work, not wq#2705's.

This is a citation slip in the one-line board note, not in the fuller ISSUE-LOG row 808
(which correctly credits both tickets) and not in the substance: the accounted-for value,
the hash, the file, and the line number are all correct — only "which of the two sibling
tickets landed it" is swapped. It does not affect whether rotation is needed (no) or
whether PR#1080 is safe to close (yes). Left as a documented nit rather than a
CONTRADICT, per the wq#2424/row 632 precedent (CONFIRMED-with-nit for a citation
slip that doesn't touch the evidence itself).

## Related but out-of-scope finding: sibling verify-pass already contradicted wq#2706

Reproduced independently: `pytest harness/tests/test_mask_replay.py::test_wq2702_corpus_burned_nothing`
(the regression test PR#1079/wq#2706 shipped specifically to pin *this* hash,
`6d4c27f66a96`) fails on current main — `assert [] == ['6d4c27f66a96']`. Root cause per
ISSUE-LOG row 817 (wq#2723, verify-pass of wq#2706): the test only pops wq#2706's own
fixture out of `tracked` and expects the value to reappear as newly `BURNED?`, but
wq#2705's overlapping `WQ2702_TRIPLE_GLUE` literal in `test_masking.py:364`
independently reconstructs the identical 60-char string, so `account()` still finds it
via that redundant source and the test's premise (this fixture is *required* to account
for the value) no longer holds. Row 817 already CONTRADICTED wq#2706 on this point and
escalated to opus-5@high; not re-filed here.

This does not change wq#2707's verdict: wq#2707's own notes cite the live
`mask_replay.py 2702` result (0 unaccounted, exit 0), not the dedicated regression test,
and that live result reproduces correctly (see above) — the value genuinely is committed
text, accounted via two redundant, legitimate, non-credential sources rather than one.
The defect row 817 found is in a proof mechanism, not in the underlying conclusion.

## Unrelated issue surfaced while probing, already tracked

`python3 -m pytest harness/tests/test_masking.py` on current main fails one test:
`test_the_mask_suite_does_not_arm_its_own_tripwire` (73 passed, 1 failed, 1 xfailed —
not 74/74 as wq#2706's notes stated, but that statement was true *at the time*, on the
main state right after its own commit). Root-caused to `128f995` ("fix(mask): bound
cfut_/kv-secret sed bodies to end in alnum", `Refs: wq#2687`, landed 20:36:25Z — an hour
after wq#2707 closed), which added `test_trailing_punctuation_after_masked_literal_survives`
containing a whole unsplit kv-secret-shaped literal (`"token: abcdefghijklmnop."`). This
is drift from a later, unrelated ticket, not a regression in wq#2707's own work, and
it's already tracked and escalated: ISSUE-LOG row 816 (wq#2726, verify-pass of wq#2687)
already CONTRADICTED that same commit's mechanism as a credential-leak regression, and
row 818 (wq#2722, verify-pass of wq#2705) filed wq#2731 (build, pri 2) specifically for
this fixture-literal fix. No new row or ticket filed here; both already exist.

## Scope note

Per verify-pass doctrine (archetype 8), this is a verification, not a re-implementation.
PR#1080 remains open (broker token lacks `write:issue` for PR comments/close, per row
808 — a pre-existing, already-logged constraint, not new). No opus-5@high escalation:
verdict is CONFIRM, not CONTRADICT.
