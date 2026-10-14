# wq#3829 — verify-pass of wq#3828 [rotation] burned credential sha256:76b4100ba253 (github)

**Verdict: CONFIRMED.** wq#3828's judgment call ("committed fixture, no rotation") holds up
under independent recomputation. No PR was shipped by wq#3828 — it closed the rotation
ticket on replay evidence alone, which is the correct outcome for this evidence.

## What wq#3828 claimed

- `mask_replay.py 3825` exits 0: 5 credential-shaped values in wq#3825 attempt 1's model
  context, 0 unaccounted.
- The burned value (github, len 36, sha256:76b4100ba253) is the committed fixture at
  `harness/tests/fixtures/wq2702_values.py:12`, echoed when wq#3825 read its own pytest
  output (`/tmp/fworker-wq3825/full_test_out.txt`).
- Same pattern as ISSUE-LOG row 800; logged as row 1290. No rotation performed.

## What I checked, independently of the tool's own narration

1. **Re-ran the probe.** `python3 harness/mask_replay.py 3825` from a clean checkout of
   `/home/lumen/foreman` reproduces the exact claim: "5 credential-shaped values in the
   run's model context, 0 unaccounted", with the github/76b4100ba253 row attributed to
   `harness/tests/fixtures/wq2702_values.py:12`.
2. **Did not trust the tool's own match.** Re-running the same tool only proves the tool is
   deterministic, not that its logic is correct. Independently recomputed sha256 over
   substrings of the fixture file's own committed text, outside `mask_replay.py` entirely:
   `wq2702_values.py`'s single `WQ2702_VALUES` entry is
   `"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8AKI" "AQQQQQQQQQQQQQQQQxoxb"` (seam-joined, 60
   chars, hashes whole to `6d4c27f66a96` — a *different* row's hash). The leading 36-char
   slice, `A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8`, hashes to **76b4100ba253** — an exact
   match to wq#3828's burned hash, computed with plain `hashlib`, no dependency on
   `mask_replay.py`'s matching code.
3. **Checked for retroactive laundering.** `git log --follow` on the fixture file shows it
   was committed 2026-09-25/26 (`fd1e0c3f`, `6310fe35`), three weeks before the wq#3825 run
   that fired the tripwire (2026-10-14T01:3x). The fixture was not added after the fact to
   manufacture an excuse.
4. **Cross-checked the raw hit record.** `_runs/wq3825/mask-hits-1.json:70` records
   `label=github, prefix=ghp_, value_len=36, value_sha256_12=76b4100ba253`, with context
   `line = 'ghp_MASKED-MASKED'` from `test_matches_masking_py_exactly[22]` — consistent with
   this being a parametrized masking-test fixture printed by pytest, not a live credential
   typed or fetched at runtime.
5. **Checked ISSUE-LOG bookkeeping.** Row 1290 (`ISSUE-LOG.md:1439`) exists, matches wq#3828's
   notes verbatim in substance, and sits at a distinct row number from its precedent (row
   800) with no collision — consistent with `issue_log_append.py`'s locked allocation, not a
   hand-picked max+1 guess.
6. **Checked the pinning test.** `test_wq2702_corpus_burned_nothing`
   (`harness/tests/test_mask_replay.py:2138`) exists and documents the same value as a
   probe wq#2702 typed into its own Write; it `skip`s here only because wq#2702's raw run
   transcript is masked at rest on this host, which is a pre-existing, documented limitation
   of the pinning tests (line 49) and not something wq#3828 introduced or could route around.

## What would have broken this verdict (and didn't)

- If the 36-char slice's hash had **not** matched 76b4100ba253 under independent
  `hashlib` computation → would mean `mask_replay.py` mis-attributed the line, and the
  real value might still be live. It matched.
- If the fixture commit postdated the wq3825 run → would mean someone planted the
  fixture to launder a real leak after the fact. It predates the run by ~3 weeks.
- If ISSUE-LOG row 1290 didn't exist or disagreed with the ticket notes → would mean the
  "closed" story wasn't actually recorded the way the ticket claims. It matches exactly.

## Scope note

wq#3828 shipped no diff/PR — it is a pure judgment ticket (run a tool, read its output,
close with "no rotation"). There is nothing to review for code-quality or regressions;
the verify surface here is entirely "is the stated evidence true," which was checked above
by recomputing it independently rather than re-trusting the same tool's output.

No escalation to judgment needed — board this as verified.
