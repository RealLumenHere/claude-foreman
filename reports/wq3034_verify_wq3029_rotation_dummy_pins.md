# wq#3034 — verify wq#3029: rotation row `d3b5aadc3f69` (aws,github)

Adversarial verify-pass (archetype 8) of `test(mask): pin wq3021 probe dummies, settle
wq3029 rotation row` (PR#1315,
`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1315`, merged to main as `7ae518bf`).
Author: claude-opus-5-5. Verifier: claude-sonnet-5@high, fresh instance, read the diff
cold.

**CONFIRMED** — no rotation needed, no contradiction of the PR.

## What the PR claims

wq#2275's mask tripwire fired pattern(s) `aws,github` on wq#3021 attempt 1's stream-json.
`mask_replay.py 3021` scored 17 credential-shaped values in that run's model context; 15
account to wq#3021's own verify-pass report
(`reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md`), leaving 2 unaccounted
(`BURNED?`): `aws` `807ae5f38db4` and `github` `3a54fc0cbc0b`. wq#3029 claims both are
runtime-built dummies from `/tmp/fworker-wq3021/probe_sk7.py` (`sk-ant-` glued to
`AKIA` + `"1"*16` and to `ghp_` + `"a"*30`), not real credentials, and closes the row by
committing them as pinned text at `harness/tests/fixtures/wq3021_values.py` plus two new
tests in `harness/tests/test_mask_replay.py`.

## What I did to try to break it

1. Read the diff cold (`git show 7ae518bf`): 2 files, 43 insertions, no deletions —
   `harness/tests/fixtures/wq3021_values.py` (new fixture) and
   `harness/tests/test_mask_replay.py` (two new tests, same shape as the existing
   `wq2687`/`wq2702`/`wq2720`/`wq2922` pin tests in the same file).
2. Tried to independently reconstruct the two hashes myself before trusting the fixture.
   First attempt hashed `prefix + value` as one blob and got a mismatch
   (`AKIA`+`"1"*16` → `a3edfcc8de6e` ≠ `807ae5f38db4`; `ghp_`+`"a"*30` → `aadcebbf9c9c`
   ≠ `3a54fc0cbc0b`) — looked like a real break. Traced it to `mask_replay.py:681-686`:
   `value_sha256_12` hashes `value` alone, prefix excluded (`prefix` and `value` are
   separate capture groups). Hashing the value-only bodies (`"1"*16` → `807ae5f38db4`,
   `"a"*30` → `3a54fc0cbc0b`) matched exactly. The test helper `_unpinned()`
   (`test_mask_replay.py:1867-1885`) already does this split correctly
   (`body = whole[len(prefix):]`, hash `body`) — my naive check was wrong, not the
   fixture. No defect here, but worth recording since it's the shape a real break would
   take.
3. Ran the PR's own regression: `pytest test_mask_replay.py -k wq3021` — 2 passed
   (`test_wq3021_pins_are_their_hashes_and_read_silently`,
   `test_wq3021_corpus_burned_nothing`).
4. Ran the ticket's prescribed probe fresh: `python3 harness/mask_replay.py 3021` — exits
   0, `17 credential-shaped values ... 0 unaccounted`. Both target hashes now read
   `[tracked]`: `github len=30 sha256=3a54fc0cbc0b prefix='ghp_'` and
   `aws len=16 sha256=807ae5f38db4 prefix='AKIA'`. Matches wq#3029's landed claim exactly.
5. Checked `account()` (`mask_replay.py:458-476`) isn't a loose match that would silently
   launder a real secret: it requires the exact string (or a documented truncation-cut
   variant) to appear in committed tracked text — `_find(needle, tracked)`, hash-exact,
   not fuzzy. A different real credential would not accidentally match this fixture.
6. Looked for independent corroboration that `probe_sk7.py` really built these two values,
   since the transcript itself isn't in this run dir (`_runs/wq3021/episode.jsonl` is a
   117-turn run's *summary* only, 1 line, no tool-call detail) and `/tmp/fworker-wq3021`
   isn't in my worktree's granted dirs. Found it: wq#3021's own report
   (`reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md:52-53`), written before wq#3029
   existed, independently documents `probe_sk7.py` producing `sk-ant-AKIA<16 digits>` →
   `'sk-ant-AKIAMASKED'` and `sk-ant-ghp_<body>` → `'sk-ant-ghp_MASKED'` — same prefixes,
   same "16 digits" length for the AWS case, same masked-output shape as
   `_runs/wq3021/mask-hits-1.json:90-105` (`"prefix":"ghp_","value_len":30` /
   `"prefix":"AKIA","value_len":16`, both timestamped `14:07:51Z`, same tool-result
   context line as the sk-key hits from the same probe run). Two independent artefacts
   (a pre-existing report and the raw hit ledger) agree with the fixture's construction.
7. Ran the full file's regression suite for drift: `pytest harness/tests/test_mask_replay.py`
   — 96 passed in 278.88s, no regressions from the two added tests.

## Verdict

wq#3029's claim holds under an adversarial read. The "hash mismatch" I found in step 2
was my own methodology bug (hashing prefix+value instead of value-only), not a flaw in
the PR — the PR's own test helper already accounts for the split correctly, and the
committed fixture reproduces both target hashes exactly. `mask_replay.py 3021` now exits
0 with 0 unaccounted, matching the PR's landed notes. Independent, pre-existing evidence
(wq#3021's own report, written before this PR existed) corroborates the two values are
`probe_sk7.py` runtime-built dummies (`AKIA`+digits, `ghp_`+body glued behind a live
`sk-ant-` prefix for the own-prefix-gap probe), not real credentials. No rotation
required. Confirming on the board.
