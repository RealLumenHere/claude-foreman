# wq#3049 verify-pass: wq#3039 [rotation] burned credential 4 values set:cd326515badb — CONFIRM

**Verdict: CONFIRM.** PR 1324 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1324) does
exactly what wq#3039 claims: it pins the two values `mask_replay.py 3037` flagged `BURNED?` as
committed, non-credential text, and its acceptance evidence (`mask_replay.py 3037` exits 0, 0
unaccounted) reproduces cleanly on a fresh run. Nothing needs rotating.

## What the PR does

Diff is test-only, two files, from the merge-base (`485e4f6c8ae5`) to the branch tip
(`b4a01e17`):

- `harness/tests/fixtures/wq3037_values.py` (new): pins `WQ3037_VALUES`, two tuples —
  `86dbbec3c5a6` (`sk-key`, prefix `sk-ant-`) and `a972ef9c0161` (`github`, prefix
  `github_pat_`) — as committed text, split every six characters at the prefix seam so
  reading the fixture doesn't re-trip the masker.
- `harness/tests/test_mask_replay.py:2236-2261`: two tests —
  `test_wq3037_pins_are_their_hashes_and_read_silently` (asserts exactly 2 pinned values,
  `_unpinned() == []`, and `masking.mask_text(text)[1] == []` — the fixture reads silently) and
  `test_wq3037_corpus_burned_nothing` (skipped if `_runs/wq3037` isn't on the host; otherwise
  replays wq#3037's actual corpus under `mask_replay.CURRENT_PATTERNS` via the shared
  `_assert_corpus_pinned` helper).

PR 1324's own branch was squash-merged to `main` as `e93c361e` (identical tree,
`git merge-base --is-ancestor e93c361e main` → true) and superseded by two independent
follow-ups already on `main` — `88f65778` (PR 1326, pins a third sk-key dummy) and `4b093413`
(wq#3040, pins the slack dummy) — each of which correctly bumped the `count ==` assertion
(2 → 3 → 4) alongside its addition. No drift.

## Reproduction (fresh, this host, `_runs/wq3037` present)

```
$ python3 -m pytest harness/tests/test_mask_replay.py -k wq3037 -v
test_wq3037_pins_are_their_hashes_and_read_silently PASSED
test_wq3037_corpus_burned_nothing PASSED
2 passed in 6.27s

$ python3 harness/mask_replay.py 3037
wq#3037: --rules auto -> current (run started 2026-09-28T14:19:22Z, legacy table retired 2026-09-20T22:35:00Z)
wq#3037: 5 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] github  a972ef9c0161  accounted: harness/tests/fixtures/wq3037_values.py:18
  [tracked] sk-key  86dbbec3c5a6  accounted: harness/tests/fixtures/wq3037_values.py:16
  [tracked] sk-key  3cfc0d4a2cf9  accounted: harness/tests/fixtures/wq3037_values.py:20   (PR 1326)
  [tracked] slack   977ab277cd08  accounted: harness/tests/fixtures/wq3037_values.py:27   (wq#3040)
  [tracked] aws     e7e8b89c2721  accounted: harness/tests/test_mask_at_rest.py:22        (pre-existing)
No rotation: every value is committed text, or a string today's masking.PATTERNS no longer reads as a credential.
```

Exit 0, 0 unaccounted — matches wq#3039's own done-notes claim exactly, and matches
`kb/ISSUE-LOG.md:1137` (`| 996 | wq#3039 ... exits 1 with 2 BURNED? values (a972ef9c0161
github_pat_, 86dbbec3c5a6 sk-ant-) |`), which is the authoritative statement of what this row
was ever responsible for: exactly the two values PR 1324 pins, no more.

## Adversarial angle tried and resolved (not a contradiction)

`_runs/wq3037/mask-hits-1.json` (the live tripwire log, 20 raw records / 14 distinct
`value_sha256_12`) contains two entries with no `context` field that don't match either pinned
value — `870b480040bc` (`github`, `len=40`) and `0bc654921d14` (`sk-key`, `len=58`) — at the
identical timestamp (`14:22:22Z`) as the two values PR 1324 pins (`len=26`, `len=51`). First
read, that looks like two unaccounted values from the same batch PR 1324 silently dropped.

It isn't: wq#3037 was itself a verify-pass probing wq#3018's *orphan-prefix backstop* — a
doubled-prefix chain (`github_pat_abc` + already-masked span + `github_pat_` + phrase) that the
backstop is supposed to cut down to just the new trailing segment instead of re-counting the
whole span. ISSUE-LOG #996 says as much: "the backstop correctly cut the second segment." The
longer-length entries (`870b...`, `0bc6...`) are the pre-cut raw regex candidates; `mask_replay.py`
scores the corpus post-backstop and only ever produces the cut (shorter) values — confirmed by
running it fresh above: neither `870b480040bc` nor `0bc654921d14` appears anywhere in
`mask_replay.py`'s output, in the accounted list or the `kv-secret`-suppressed audit list,
because they're not independent detections — they're the same underlying dummy text at a wider
match boundary the backstop already resolves. `mask_replay.py`'s `corpus()`/`replay()` re-derive
from the snapshot/transcript corpus directly under today's patterns (`mask_replay.py:246-274`,
`:640`); it does not read `mask-hits-N.json` at all, so a live-log artifact of the raw pre-backstop
match width is expected to disappear on replay and is not evidence of a rotation gap.

## Falsifiability check (wq#2630)

`_assert_corpus_pinned` (`test_mask_replay.py:1903-1935`) pops the fixture module out of the
tracked-text set and re-replays; it asserts whatever comes loose is a subset of what the popped
fixture claimed to explain. That means removing either pinned value's committed text *would*
turn up in `unaccounted()` — this is the same helper reused verbatim across ten prior rows
(wq#2687, 2702, 2720, 2787, 2913, 2922, 3021, and now 3037), so the probe is a proven-falsifiable
pattern, not new-and-untested for this ticket.

## Disposition

`wq#3049` closes CONFIRM. PR 1324 stands as shipped (already on `main` as `e93c361e`); nothing to
rotate, nothing to re-implement.
