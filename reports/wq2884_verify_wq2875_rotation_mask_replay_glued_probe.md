# wq#2884: verify-pass on wq#2875 (commit `95e2ad8`, rotation row for wq#2848 aws+slack)

Date: 2026-09-28. Archetype 8 adversarial verify, fresh instance. Author: claude-opus-5-5
(wq#2875, lane fleet). Verifier: claude-sonnet-5@high (this ticket, lane build).

## Verdict: CONFIRM

Commit `95e2ad8` ("fix(mask): make replay read multi-pass hits like tripwire", merged to
`main`) touches `harness/mask_replay.py`, `harness/masking.py`,
`harness/tests/fixtures/wq2875_values.py`, `harness/tests/test_mask_replay.py`, and one
report cell in `reports/wq2846_verify_wq2836_ir_triage_truncate_before_mask.md`. Read the
diff cold, tried to break the refactor and the identity-based dispatch it adds, ran the
required replay probe and the full test suites. No contradiction found.

## What the fix does

- **Root cause claimed**: `mask_replay._hits()` used to re-derive matches with one
  `finditer` per rule over the raw text, single pass. `masking.scan()` (the live
  tripwire) runs up to `len(PATTERNS)` passes (wq#2720), so a value only freed after an
  earlier rule's truncated match masks a foreign prefix next to it is invisible to a
  single pass. wq#2848's glued probe (`ghp_<body>AKIA<16>xoxb-<body>`) fired github, aws,
  and slack live, but the replay's single-pass read only ever saw github — so its
  "0 unaccounted" verdict never actually scored the ticket's own aws/slack values.
- **Fix**: extracted `scan()`'s existing multi-pass loop verbatim into a new
  `masking.raw_matches(text) -> ([(label, prefix, value)], masked_text)`; `scan()` now
  calls it. `mask_replay._hits()` reads through `raw_matches()` when
  `patterns is CURRENT_PATTERNS and skip_masked_output` (today's table, tripwire-shaped
  call), and falls back to the old per-rule single-pass loop otherwise (legacy tables,
  or a caller that explicitly wants the old semantics).
- **Side fixes**: pinned a distinct false-positive (`fixtures/wq2875_values.py`) — the
  suppression-reason string `"pointer+call"` that kv-secret misread as a value while
  this ticket's own worker was investigating wq#2848's hit dump — and reworded one report
  table cell in `reports/wq2846_*.md` that had typed a whole `sk-` + 16-`Q` shape as one
  unbroken literal, re-arming the tripwire on read (ISSUE-LOG rows 886, 889).

## What I checked

| Claim | How I checked it | Result |
|---|---|---|
| The extracted loop in `raw_matches()` is byte-identical to the one it replaced in `scan()`, not silently altered during the move | `git show 95e2ad8 -- harness/masking.py`, diffed the removed lines from `scan()` against the added lines in `raw_matches()` | Identical line-for-line (same body, same comments) |
| Required probe: `python3 harness/mask_replay.py 2848` reproduces "0 unaccounted" and lists aws/slack | Ran it directly against the real `_runs/wq2848/` corpus | `5 credential-shaped values … 0 unaccounted`, including `[tracked] aws … sha256=e69bbe113521` and `[tracked] slack … sha256=254aa248acb4`, both `accounted:` against committed fixtures |
| aws/slack values are genuinely pre-existing committed dummies, not live secrets | Traced `accounted:` pointers: aws → `harness/tests/fixtures/wq2702_values.py:12`; slack → `harness/tests/test_mask_replay.py:2038` (this PR's own new test, containing the literal glued probe `"AKI""A"+"Q"*16` / `"xox""b-"+"1234567890"+"1"`) — both are pre-existing or newly-added test literals, `grep`-confirmed in `test_masking.py:452` (`AKIA`+16 `Q`s, `xoxb-`+`12345678901`, the exact wq#2702 triple-glue fixture) | Confirmed dummies, matches PR's "no rotation" claim |
| Full masking + mask_replay test suites still pass | `python3 -m pytest harness/tests/test_masking.py harness/tests/test_mask_replay.py -q` | 198 passed (110 masking, 88 mask_replay) |
| The 3 new regression tests for this fix pass and are the ones doing the falsifying work | `pytest -k "wq2875 or glued_line or wq2848_replay"` | 3 passed: `test_replay_reads_what_the_tripwire_reads_on_a_glued_line`, `test_wq2848_replay_reproduces_every_live_hit`, `test_wq2875_pins_are_their_hashes_and_read_silently` |
| Probe is genuinely falsifiable (not drawn only from the population the fix was designed around) | `test_replay_reads_what_the_tripwire_reads_on_a_glued_line` builds its own fresh glued literal (different body characters than wq#2848's) and asserts `mask_replay.replay()`'s labels/hashes equal `masking.scan()`'s live read; against the pre-fix single-pass `_hits`, this would fail because slack/aws would be dropped, per the mechanism in the docstring | Confirmed falsifiable — this is exactly the shape the old code got wrong |
| ISSUE-LOG rows 886 / 889 the PR notes cite actually exist and match | `grep -n "^| 88[6-9]" kb/ISSUE-LOG.md` | Row 886: wq#2875, same root-cause/fix wording. Row 889 (wq#2848 entry): same report-cell wording as the diff |

## Adversarial angles tried, not confirmed as flaws

- **Does the `patterns is CURRENT_PATTERNS` identity check silently skip the fix for a
  legitimate "today's rules" caller that doesn't hold the exact singleton object?**
  Checked `harness/mask_triage.py`: its historic rule sets are built via `rules_at()`,
  which `exec()`s a fresh module per commit sha and so never aliases `CURRENT_PATTERNS`
  — but its `"head"` rule set (`triage_ticket`, line 295) is appended as
  `("head", mask_replay.CURRENT_PATTERNS, True)`, using the real singleton directly. So
  the one live "today's rules" consumer outside `mask_replay.replay()`'s own defaults
  does get the fix; the identity check does not create a second blind spot.
- **Does routing legacy-table replays through the old single-pass loop reintroduce the
  bug for historic scoring?** No — that's unchanged pre-existing behavior (legacy tables
  never had the multi-pass masking this fixes), not a regression this PR causes.
- **Could a value found only on pass 2+ of `raw_matches()` fail the caller's
  `text.find(prefix + value)` line-lookup (`mask_replay.py:668`) because the substring
  only exists in the partially-masked `cur`, not the original `text`?** Reasoned through
  the mechanism (a foreign-prefix stop leaves the freed literal untouched in both raw and
  intermediate text; only the boundary next to it changes) and then checked it
  empirically: the real `mask_replay.py 2848` run above correctly resolved `seen in:` and
  `line:` for both the aws and slack records, so the lookup does not break in practice.

## Why this confirms

Every claim is independently checkable and held up: the refactor is a verified no-op
extraction, the replay probe reproduces the PR's exact claimed output against the real
`_runs/wq2848/` corpus, both previously-unaccounted values trace to genuine pre-existing
test literals (no live credential), the full relevant test suites pass (198/198), and the
one identity-check edge case I went looking for a gap in (`mask_triage`'s "head" rule set)
turned out to already be wired correctly. No basis to contradict; no escalation warranted.
