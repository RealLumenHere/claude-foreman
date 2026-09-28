# wq#3067 verify-pass: wq#3055 [rotation] burned credential 2 values set:606923f6d3fd (kv-secret) — CONFIRM

**Verdict: CONFIRM.** PR 1347 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1347,
content-identical to what actually landed on `main` as `106a4213`, then tightened by a
same-day follow-up `0c7af93c`) does exactly what wq#3055 claims: both kv-secret-shaped
hashes from the `606923f6d3fd` set — `2f5ecce736c5` (`HR_AUTH_PASSWORD=`, len 22) and
`cb5e89f275c8` (`HR_SECRET_KEY=`, len 24) — are doc placeholders from the upstream
harnessrouter self-hosting guide mirror wq#2120 read while auditing its own
`read_dir_sweep` hits, not a Lumen credential. Nothing to rotate.

## What the PR does

Two files, from `main`'s tip at the time (`4b4e69f7`) forward:

- `harness/tests/fixtures/wq2120_values.py` (new): pins the two values as
  `WQ2120_VALUES`, each split into 6-char literal chunks so the file itself carries no
  whole credential shape, with a header explaining the leak path (third-party doc, doc's
  own `docker run` examples quote placeholder secrets) and citing the exact
  `read_dir_sweep` fingerprints (`527602abdce7`, `aea20d56e205`) wq#2120 already acked.
- `harness/tests/test_mask_replay.py`: adds `test_wq2120_pins_are_their_hashes_and_read_silently`
  (pins hash-check + `masking.mask_text` reads the fixture silently),
  `test_wq2120_pins_are_the_sweep_acks_wq2120_reviewed` (the pinned spans hash to the same
  fingerprints `read_dir_sweep.REVIEWED_HIT_FINGERPRINTS` already holds — not a fresh
  judgment, the same one wq#2120 made), and `test_wq2120_corpus_burned_nothing`
  (`_assert_corpus_pinned`, skipped only if `_runs/wq2120` is absent from the host).

`wq show 3055`'s done-notes and ISSUE-LOG row 1008 match this diff exactly.

## Reproduction (fresh, this host, `_runs/wq2120` present)

```
$ python3 -m pytest harness/tests/test_mask_replay.py -k wq2120 -v
test_wq2120_pins_are_their_hashes_and_read_silently PASSED
test_wq2120_pins_are_the_sweep_acks_wq2120_reviewed PASSED
test_wq2120_corpus_burned_nothing PASSED
3 passed in 9.64s

$ python3 harness/mask_replay.py 2120
wq#2120: 10 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret len=22 sha256=2f5ecce736c5 prefix='HR_AUTH_PASSWORD='
      accounted: harness/tests/fixtures/wq2120_values.py:17
  [tracked] kv-secret len=24 sha256=cb5e89f275c8 prefix='HR_SECRET_KEY='
      accounted: harness/tests/fixtures/wq2120_values.py:19
  ...(8 more, already tracked/rule-fixed from prior rows)
No rotation: every value is committed text, or a string today's masking.PATTERNS no
longer reads as a credential.
```

Exit 0, 10 values / 0 unaccounted, matching wq#3055's done-notes and row 1009's
independent reproduction from a scratch worktree of the same commit.

## Adversarial angles

**Are the pins the actual burned values, or a coincidental-shape dummy?** Checked
`mask-hits-1.json` directly rather than trusting the commit prose: `2f5ecce736c5` and
`cb5e89f275c8` each occur exactly once in the original run's recorded hits, both labeled
`kv-secret`, with `context` text that independently names the source
(`.../research/harnessrouter-ref/src/docs/self-hosting-guide.md:118` and `:541`,
`HR_AUTH_PASSWORD=`/`HR_SECRET_KEY=`, "hyphenated English words, zero digits") — this
context was captured by the original run, so it's immutable evidence independent of the
mirror file's current (out-of-grant) state. `mask_replay.account()`'s body-length check
(`harness/mask_replay.py:458-476`) is the wq#2740 fix: since both bodies are 22/24 chars
(well over the 12-char `TRUNCATED_FLOOR`), the search key is the body alone, not
prefix+body — a coincidence-proof match, unlike the short-body class wq#3062 (row 1012)
found still-open elsewhere. Reconstructing the split literals confirms both decode to
literal English doc phrases (`the-password-you-chose`, `a-long-random-passphrase`), not
random or credential-shaped text.

**Does one committed file actually explain both hashes, or is the pin doing nothing?**
`test_wq2120_pins_are_their_hashes_and_read_silently` calls `_unpinned`, which recomputes
`masking.value_sha12(body)` for each pin tuple and requires it to equal the claimed sha —
this passed, so the fixture's literal bytes are exactly the values named in the ticket,
not lookalikes.

**Fingerprint reuse claim** — `test_wq2120_pins_are_the_sweep_acks_wq2120_reviewed`
independently re-derives each fingerprint via `read_dir_sweep.reviewed_hit_fingerprint()`
and asserts the set equals `{"527602abdce7", "aea20d56e205"}` and is a subset of
`read_dir_sweep.REVIEWED_HIT_FINGERPRINTS` — passed, so this genuinely reuses wq#2120's
own prior review rather than asserting a fresh, untested judgment.

**Self-trip check** — a fixture built to explain a kv-secret false-positive could itself
carry a live kv-secret shape (the exact class row 1145/wq#3021 hit). The shipped commit
had this bug in the *test's docstring* (not the fixture): `60f77b68`/`106a4213` originally
wrote `"""wq#3055's verdict, pinned: HR_AUTH_PASSWORD= / HR_SECRET_KEY= ` + `placeholders..."""`,
gluing the two labels straight to `=` in prose. Follow-up `0c7af93c` (same day, still
`Refs: wq#3055`) rewrote it to `"the HR auth-password and secret-key values are
placeholders..."`, removing the label-adjacent-to-`=` shape. Confirmed the fixture file
itself was never the problem — `masking.mask_text(fixture_text)[1] == []` passes both
before and after, and the assertion is exercised directly by
`test_wq2120_pins_are_their_hashes_and_read_silently`.

## Falsifiability check (wq#2630)

`_assert_corpus_pinned` (shared helper, unmodified, used by ~12 prior rows) pops the
fixture's committed text out of the tracked-file set and re-replays, asserting anything
that comes loose is a subset of the fixture's own pinned hashes — so removing the pin
would surface both `2f5ecce736c5` and `cb5e89f275c8` as unaccounted again. This is
exercised inside `test_wq2120_corpus_burned_nothing`, which is *not* skipped on this host
(`_runs/wq2120` exists), so the probe ran for real, not vacuously.

## Orthogonal finding (not attributable to this diff)

`test_masking.py::test_the_mask_suite_does_not_arm_its_own_tripwire` fails on current
`main` — but on two files neither touched by wq#3055's diff:
`reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md` (pre-existing, already logged open
at ISSUE-LOG row 1145) and `reports/wq3062_judgment_wq3061_rotation_accounted_in_false_positive.md`
(new; confirmed absent at the PR's merge-base `4b4e69f7`, so it landed from a sibling
ticket's own commit, not from wq#3055). Neither file is in `60f77b68`'s two-file diff.
Not this ticket's fix to make — flagging for whoever next touches `test_masking.py`.

## Disposition

`wq#3067` closes CONFIRM. wq#3055's fix (landed on `main` as `106a4213` + `0c7af93c`)
stands as shipped; nothing to rotate, nothing to re-implement.
