# wq#2668 — verify of wq#2663 (PR#1028, c6b42d3 on top of a35a289)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2663 was the rotation ticket for a mask tripwire on `cloudflare` fired by wq#2630
attempt 1's stream-json (sha256 `740e963a761d`). Its ruling: no credential, nothing
rotated — the value is a 14-char `value[:14] + "..."` preview of a committed 22-char
`cfut_` test fixture. A 14-char preview leaves a 9-char body after `cfut_`, under any
body floor, so `masking.untruncated`'s existing retry (wq#2648) never fired and the
fixture paged as burned. The fix (`masking.untruncated_whole`) retries a sub-floor cut
body WITH the rule's own literal prefix attached, requiring the reconstructed
`prefix+cut` string to (a) reach the overall floor (12) and (b) have the committed
tree hold that exact prefixed string — not just the bare body.

I read the diff cold (`git show c6b42d3`: `harness/masking.py` +17,
`harness/mask_replay.py` +14/-6, `harness/burn_filter.py` +6/-1, plus tests), traced
every call site by hand, and independently re-ran the ticket's own probe rather than
trusting its notes. Did not re-implement.

## Per claim

| wq#2663 claim | Result | Evidence |
|---|---|---|
| `740e963a761d` is a 14-char preview of a committed 22-char `cfut_` fixture, not a credential | CONFIRMED | Ran `mask_replay.py 2630 --rules current` against the pre-fix commit (`a35a289`, PR's own merge-base): reports `[BURNED?] cloudflare ... sha256=740e963a761d accounted: None`. Same command against the fix commit (`c6b42d3`) reports `[tracked] cloudflare ... sha256=740e963a761d accounted: harness/tests/test_mask_replay.py:1145` and `0 unaccounted` overall — the exact hash named in this ticket's title flips from burned to accounted under the fix, reproduced from the run's own frozen corpus, not from the PR's say-so. |
| `masking.untruncated_whole` closes the leak without over-excusing a genuinely different/live secret that merely shares a short prefix | CONFIRMED | Traced `masking.py:207-213`, `burn_filter.py:216-222`, `mask_replay.py:379-403` by hand: the body-only cut must reach `TRUNCATED_BODY_FLOOR=6`, and `prefix+cut` must reach the overall floor (12) AND be found verbatim in the tracked tree — the bare body is not enough. Adversarial check against the pre-existing regression `test_the_truncation_retry_still_burns_a_cut_unknown_value` (`test_burn_filter.py:174`, untouched by this diff): body `"abcdefghijk"` (11 chars, sub-floor) is committed alone but its prefix `"TOKEN="` is not; hand-traced the new fallback path and confirmed it reconstructs `"TOKEN=abcdefghijk"`, which the fixture tree does *not* hold, so the value correctly stays BURNED. No over-excusal regression. |
| Sub-floor retry only applies to printer-truncated previews, not to live/ordinary source | CONFIRMED | `untruncated_whole`/`untruncated` only fire when the value literally ends in the `"..."` truncation marker (a printer artefact) — ordinary source lines never reach the new branch, so the repo-wide sweep tests (`test_reading_ordinary_source_does_not_page`, `test_no_tracked_module_pages_when_a_worker_reads_it`) see no new behavior. |
| Fix ships with test coverage, no regressions | CONFIRMED (one unrelated pre-existing failure noted below) | Full suite on the fix commit: `harness/tests/test_masking.py` 65/65 passed; `harness/tests/test_burn_filter.py` + `harness/tests/test_mask_replay.py` together: **172 passed, 1 failed** in 24m22s. The failure (`test_wq1819_transcript_burned_nothing`, `assert len(records) == 1` got 3) reproduces **identically** on the pre-fix commit (`a35a289`) with the same 3-vs-1 mismatch and `unaccounted(records) == []` still holding — environmental drift in a pinned real-corpus test (the live tracked tree has grown since it was pinned), predating and unrelated to this diff. Both new tests for this exact fix (`test_a_preview_cut_under_the_floor_is_accounted_with_its_prefix` in `test_mask_replay.py`, `test_a_preview_cut_under_the_body_floor_is_accounted_with_its_prefix` in `test_burn_filter.py`) pass, as do two other real-corpus pinned regressions (`test_wq2288_transcript_burned_nothing`, `test_the_live_rules_would_not_have_filed_wq2307`). |

## Probe run (this ticket's acceptance check)

`python3 harness/mask_replay.py 2630 --rules current`, fix commit: `wq#2630: 2
credential-shaped values in the run's model context, 0 unaccounted` — both the
`kv-secret` and `cloudflare` hits (including `740e963a761d`) show `[tracked]`. Same
command, pre-fix commit: `1 unaccounted`, `[BURNED?] cloudflare ... sha256=740e963a761d`.
The probe can fail (and does, on the parent commit) and passes on the fix — not a
probe drawn only from the population the fix was designed around.

No rotation needed, no follow-up required from this verify-pass. The one failing test
found (`test_wq1819_transcript_burned_nothing`) is a live-corpus drift issue outside
wq#2663's scope; not filing a new ticket for it here since `unaccounted(records)` still
returns `[]` for that ticket too — it is a stale record-count pin, not an unaccounted
credential.
