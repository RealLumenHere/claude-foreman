# wq#3136 — verify wq#3135 (rotation, burned credential 3 values set:51349b216532)

**Verdict: CONFIRMED.** wq#3135's diff is correct: the row's 3 alarmed values, and the
10 sibling probe values it also pins, are synthetic identifier-chain literals from
wq#3134's echo-guard boundary probes — no credential material, nothing to rotate.

## What wq#3135 shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1413 (commit `0f75bbc2` /
`14de18f7` on branch `fix/wq3135-rotation-burned-credential-3-values-set--a1`), two
files, purely additive (no production code touched):

- `harness/tests/fixtures/wq3134_values.py` — 13 `(sha12, label, value)` pins, each
  value built from short string-literal fragments (never a contiguous credential-shaped
  literal) plus a prose header explaining the source of each.
- `harness/tests/test_mask_replay.py` — two new tests following the established
  `test_wqNNNN_pins_are_their_hashes_and_read_silently` /
  `test_wqNNNN_corpus_burned_nothing` shape used by every prior row in this chain
  (wq2687, wq3076, etc.).

## Verification performed

1. **Re-ran the shipped tests against real data**, not just import-checked them:
   `pytest harness/tests/test_mask_replay.py -k wq3134 -v` → both new tests **PASSED**.
   `test_wq3134_corpus_burned_nothing` is `skipif`-guarded on
   `os.path.isdir(WQ3134_RUN)`; on this host `/home/lumen/foreman/_runs/wq3134` exists,
   so the test actually re-scanned wq#3134's live corpus with
   `mask_replay.replay(3134, ...)` rather than skipping — a real, falsifiable check ran.

2. **Recomputed every pinned hash independently.** Loaded the fixture module and ran
   `masking.value_sha12(value)` for all 13 entries: all 13 match their claimed sha12
   exactly.

3. **Reconstructed the ticket's own title hash from ground truth.** wq#3135's title
   carries `3 values set:51349b216532`, which `fworker.py:1139`
   (`sha256("\n".join(sorted(hashes))).hexdigest()[:12]`) derives from the *unsuppressed*
   (`FIRED`) hits only. Read wq#3134's own `_runs/wq3134/mask-hits-1.json` (12 logged
   events, values never printed — only `label`/`prefix`/`value_len`/`value_sha256_12`).
   Exactly 3 entries lack a `suppressed` key (i.e., the echo guard did NOT suppress them,
   so the general kv-secret rule fired and masked them): `763e7406409b`, `7a96ee6af9af`,
   `6d2726b35d73`. `sha256("\n".join(sorted([...])))[:12]` on those three ==
   **`51349b216532`** — an exact match to the ticket title, and all three are present
   verbatim in the fixture's first 3 pins. This ties the fixture directly to the actual
   alarm, not just to a plausible-looking story.

4. **Checked every value for credential shape.** All 13 bodies are dotted/underscored
   chains of `self`/`a`/`b`/`c`/`tail`/`SELF`/`ABC`/`x`-runs/English words — no digits, no
   base64/hex runs, no random-looking material. The 13th is `test_masking.py`'s existing
   committed `..._correct_horse_battery_staple_extra_words` value (line 1858 there) with
   `_here_too` appended, exactly as the fixture's header claims.

5. **Ran the full `test_mask_replay.py` suite** (116 tests) to check for regressions —
   the change is purely additive (new fixture file + two new test functions, no edits to
   existing lines), so a break was unlikely; full run confirmed green (see notes below on
   runtime — the suite takes >120s so it was backgrounded, and finished clean, no
   failures/errors).

## Adversarial angles tried and ruled out

- Tried to find a value among the 9 non-alarmed, non-13th pins that could be a real
  secret hiding behind a synthetic-looking name — none carry digits or entropy consistent
  with a real token; all trace to described probe boundary variants (qualifier segment
  count, `_` gap count, tail length) matching wq#3133's guard bounds
  (`test_masking.py:1852-1864`).
- Checked whether `mask-hits-1.json`'s 9 *suppressed* ("echo") entries should have
  matched the fixture's other 9 pins directly — they don't overlap by hash. This is
  expected, not a defect: those 9 pins are described as "read in the transcript only"
  (separate probe invocations never routed through the live masking-gate log), and the
  corpus test's own falsifiable check (`mask_replay.replay()` re-scanning the raw
  transcript, independent of `mask-hits-1.json`) is what actually vouches for them — and
  it passed, including the "loose remainder is a subset of the fixture's own pins" check
  in `_assert_corpus_pinned` (`test_mask_replay.py:2010-2016`).
- Confirmed the probe's "leak" is the ordinary self-inflicted kind this whole ticket
  chain (wq2687, wq3076, ...) already established as non-credential: the model typed the
  literal chain into its own `python3 -c` probe code, so the value was in its context the
  moment it typed it, independent of whether the tool-result masking gate later caught or
  suppressed the echo.

## Conclusion

wq#3135's diagnosis and diff hold up under an adversarial pass with fresh evidence
(independent hash recomputation, independent title-hash reconstruction from the raw
mask-hits log, and a real — not skipped — corpus replay). No rotation needed; the PR is
sound as shipped. Confirming on the board, no escalation.
