# wq#3073 [verify] wq#3063 — kv-secret rotation row set:b5d5fdffc861 (fired on wq#3036)

**Verdict: CONFIRM.** wq#3063's PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1356,
merged as `2e8dc885`) does what it claims. No contradiction found; no escalation to judgment.

## What I checked

Read the diff cold: two new files, `harness/tests/fixtures/wq3036_values.py` (24 lines, six
pinned `(sha, label, value)` tuples) and a 26-line addition to
`harness/tests/test_mask_replay.py` (`test_wq3036_pins_are_their_hashes_and_read_silently`,
`test_wq3036_corpus_burned_nothing`), following the same pattern this file already uses for
~10 prior rotation rows (wq2687, wq2702, wq2720, wq2787, wq2875, wq2913...). No
re-implementation attempted.

### 1. Ran the ticket's own probe, fresh

```
$ python3 /home/lumen/foreman/harness/mask_replay.py 3036
wq#3036: --rules auto -> current (run started 2026-09-28T18:20:35Z, legacy table retired 2026-09-20T22:35:00Z)
wq#3036: 6 credential-shaped values in the run's model context, 0 unaccounted
  ... (6 records, all [tracked])
No rotation: every value is committed text, or a string today's masking.PATTERNS no longer reads as a credential.
$ echo $?
0
```

Matches the PR notes exactly: 6 values, 0 unaccounted, exit 0.

### 2. Cross-checked the raw evidence, not just the fixture's own claim

`_runs/wq3036/mask-hits-1.json` (the frozen corpus, 23 records) contains 5 of the 6 pinned
shas verbatim, at the same label/prefix/length the fixture claims:
`9fa83f4bd042` (token=), `be31c6a7db60` (auth_token=), `55ba099e29b2` (secret=),
`14ac1d284ecc` (signing_secret=), `c30198468832` (api_secret=). The sixth,
`bf43137fb255`, is **not** new — it's a pre-existing committed dummy in
`harness/tests/fixtures/wq3036_sweep_corpus.py:24` (`pending_rotation_secret =
queued_epoch_signing_material`), blamed to 2026-09-02, predating both wq#3036 and wq#3063.
The PR's fixture correctly recognizes this one as already-accounted rather than re-pinning
a duplicate.

### 3. Verified the pin mechanism is not vacuous

`_unpinned()` (test_mask_replay.py:1867) is the helper that decides a pin "holds": it
recomputes `masking.value_sha12(body)` and requires it to equal the claimed sha. I ran it
standalone and tampered with a copy of the fixture in-process (no files touched):

```
correct bad-list (should be empty): []
tampered sha (should report bad): ['000000000000']
tampered value (should report bad): ['9fa83f4bd042']
```

Confirms the pin check actually discriminates — a wrong hash or altered value fails it, so
the passing test is real evidence, not a tautology.

### 4. Values are what they're claimed to be

All six bodies are lowercase dictionary words joined by `_` (`queued_epoch_signing_material`,
`rotate_epoch_signing_material`, `pending_worker_claim_value`, `pending_rotation_material_value`,
`pending_rotation_material`, `queued_handshake_material_value`) — no digits, no uppercase,
no real entropy. Consistent with the claimed leak path: wq#3036 ran a `python3 -c` probe
feeding `masking.mask_text()` literal `<name> = <phrase>` samples instead of building the
body at runtime, which is exactly the anti-pattern `harness/prompts/worker.md:76-88`
documents (verified this file/text exists and matches, not just cited).

### 5. Full regression run

```
$ pytest harness/tests/test_mask_replay.py -q
105 passed in 362.65s
```

No regressions from the two new tests.

### 6. Pre-fix reproduction — attempted, inconclusive by design, not a defect

Tried the wq#3070-style approach (clone repo, checkout the pre-fix parent `c6a2cd3b`, rerun
the probe). This did **not** reproduce a clean pre-fix failure: `mask_replay.py`'s
`foreign_repos()`/`committed_text()` resolve any corpus item path containing the literal
string `/home/lumen/foreman` against the **live** tree at that path regardless of where the
script itself runs from (`FOREMAN_ROOT` defaults to the literal `/home/lumen/foreman`,
independent of `REPO_ROOT`). Since wq#3036's original transcript recorded absolute paths
under `/home/lumen/foreman`, the clone's replay still picked up the live (already-fixed)
`wq3036_values.py`. Reproducing a true "before" run would require reverting
`/home/lumen/foreman` itself, which is a write-frozen primary tree (guard.sh wq#1719) — out
of scope for a verify pass. This is a property of the replay tool's cross-repo accounting,
not a flaw in wq#3063's diff; items 1-5 above are direct, sufficient evidence for the
claim, and ISSUE-LOG row 1014 (kb repo, timestamped 2026-09-28T18:53:08, same session)
independently records the pre-fix state as "exits 1, 6 BURNED?".

## Minor note, non-blocking

wq#3063's `wq done` notes say "ISSUE-LOG row 10". The actual row is **1014**
(`/home/lumen/kb/ISSUE-LOG.md:1155`), not row 10 (an unrelated 2026-09-02 alerting entry).
Content of row 1014 is accurate and consistent with this PR. Read as a transcription
shorthand, not a factual error in the delivered fix — noting it so it doesn't get relied on
verbatim later.

## Conclusion

No credential was burned. The six kv-secret hits are wq#3036's own probe/fixture text,
now pinned and accounted. `mask_replay.py 3036` exits 0. Nothing to rotate. Confirming on
the board.
