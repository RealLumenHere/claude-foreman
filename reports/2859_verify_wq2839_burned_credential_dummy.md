# wq#2859 — verify-pass of wq#2839 (PR#1171): burned credential sha256:30f8df099069 (kv-secret)

**Verdict: CONFIRM.** wq#2839's disposition — the flagged value is a hex counting-run
dummy wq#2787 typed into its own `masking.scan` probe, not a Cloudflare token, and nothing
needs rotating — holds under independent re-derivation and a live re-run of the ticket's
own prescribed check.

## What was verified

1. **Diff scope.** PR#1171 (`fix/wq2839-rotation-burned-credential-sha256-30f8df-a1`,
   merge-base `53d4efa`) touches exactly three files: `harness/prompts/worker.md` (+5/-1),
   `harness/tests/fixtures/wq2787_values.py` (new, 18 lines), `harness/tests/test_mask_replay.py`
   (+30, two new tests). No production masking logic changed — this is a pin-the-dummy +
   widen-the-prompt-rule fix, matching the ticket's own instruction to settle the row on
   `mask_replay.py` evidence and rotate nothing if the replay comes back 0 unaccounted.

2. **Hash claim, independently recomputed.** The fixture's pinned value is
   `"abcdef0123456789" "abcdef0123456789" "abcd"` (36 chars, string-literal seam-split so the
   file itself reads clean under `masking.mask_text`). Recomputed outside the test:
   `sha256(value)[:12] == "30f8df099069"` — matches the ticket title's hash exactly.
   `masking.value_sha12` (`harness/masking.py:464`) is the same sha256[:12] scheme the
   rotation ticket, `mask_replay`, and `mask_triage` all key off, per its own docstring
   (wq#2341) — not a locally-invented comparison.

3. **The ticket's own prescribed probe, re-run live.**
   `python3 /home/lumen/foreman/harness/mask_replay.py 2787` (exactly as wq#2839's detail
   text instructs) against the real `_runs/wq2787/` on this host reports:
   `1 credential-shaped values in the run's model context, 0 unaccounted`, with the one
   value `[tracked] kv-secret len=36 sha256=30f8df099069 prefix='CF_API_TOKEN='` shown
   `accounted: harness/tests/fixtures/wq2787_values.py:11` and its context line showing a
   `python3 -c` probe of `masking.scan('CF_API_TOKEN=<kv-secret 36 chars>')` — a self-authored
   test of the masking module, not a value pulled from any real secret store. Ticket text
   says zero-unaccounted settles the row without rotation; that is exactly what this run
   shows, on the actual evidence path, not just in the new pytest.

4. **Falsifiable before/after, not just a passing test.** Replayed wq#2787 programmatically
   with the fixture's text excluded from `tracked` (simulating "PR not shipped"):
   `mask_replay.unaccounted(...)` returns exactly `['30f8df099069']` — the original alarm,
   reproduced. With the fixture module present (as `test_wq2787_corpus_burned_nothing` and
   the CLI both run it), the same replay returns `unaccounted == []`. The probe can fail
   (and does, with the fixture pulled) — this isn't a test that was structurally guaranteed
   to pass regardless of what shipped (wq#2630 concern), and it isn't scoped only to inputs
   drawn from inside the fix (it replays the full 17-item wq#2787 corpus, not a hand-picked
   subset).

5. **Both new tests pass.**
   `pytest harness/tests/test_mask_replay.py -k wq2787` → 2 passed
   (`test_wq2787_pins_are_their_hashes_and_read_silently`,
   `test_wq2787_corpus_burned_nothing`; the latter is not skipped — `_runs/wq2787/` exists
   on this host). `test_wq2787_pins_are_their_hashes_and_read_silently` independently checks
   `masking.value_sha12(value) == sha` and `masking.mask_text(text)[1] == []` (the fixture's
   own source doesn't retrigger the tripwire it exists to close).

6. **worker.md rule widening matches the actual root cause.** The added rule text names the
   env-assignment shape (`NAME_TOKEN=`/`KEY`/`SECRET`/`PASSWORD`/`PAT` + `=`/`:` + 12+ word
   chars) that wq#2787's `CF_API_TOKEN=<hex run>` probe fell into, and gives a seam-split
   fix (`"CF_API_TOKEN=" + "ab" * 18`) of the same shape used in the new fixture — the
   documentation change and the code fix are the same pattern, not two unrelated edits
   bolted together.

7. **Report placement and ticket bookkeeping.** `wq show 2839` confirms `status: done`,
   notes cite PR#1171 and this exact disposition; ISSUE-LOG row 872 (already `SOLVED`)
   matches this PR's file list and rationale verbatim. This report lives under
   `reports/`, not the repo root (wq#2805).

## What I tried to break

- Did not trust the pytest assertions alone — recomputed the sha256 by hand and re-ran the
  ticket's own `mask_replay.py 2787` CLI directly against live `_runs/` data, independent of
  the new test file.
- Checked whether the "0 unaccounted" result depends on the fixture at all, or would hold
  even without it (i.e. whether the test is vacuous): pulled the fixture text out of
  `tracked` and re-replayed — the alarm value comes back unaccounted, confirming the fixture
  is load-bearing, not decorative.
- Checked the corpus isn't degenerate: `mask_replay.corpus(2787)` returns 17 items, and the
  replay resolves to exactly 1 credential-shaped record (matching `mask-hits-1.json`'s first
  entry: `label=kv-secret`, `prefix=CF_API_TOKEN=`, `value_len=36`,
  `sha256_12=30f8df099069`), not zero or many — no evidence of cherry-picking.
- Checked the two other kv-secret values in `mask-hits-1.json` (`ceb969f320cb`,
  `166e2282ed20`) — both are `suppressed` (audit-only, per-rule) in the original run, not
  `unaccounted`, so they were never in scope for wq#2839's rotation row (which is keyed by
  the specific burned value, per wq#2341) and this PR correctly leaves them alone.
- Ran the full `test_mask_replay.py` suite (85 tests) rather than just the two new ones, to
  check for collateral breakage from the shared `_assert_corpus_pinned` helper reuse.

No contradiction found. Nothing escalates to opus-5@high judgment.

## Evidence index

- `agents/work-queue` ticket text: `wq show 2839`
- PR#1171: `http://100.121.17.5:3000/lumen/claude-foreman/pulls/1171`,
  branch `fix/wq2839-rotation-burned-credential-sha256-30f8df-a1`
- `harness/prompts/worker.md` (diff), `harness/tests/fixtures/wq2787_values.py`,
  `harness/tests/test_mask_replay.py` (new tests + shared `_assert_corpus_pinned`)
- `harness/masking.py:464` (`value_sha12` definition)
- `/home/lumen/foreman/_runs/wq2787/mask-hits-1.json` (original alarm record)
- Live re-run: `python3 harness/mask_replay.py 2787`
- `foreman/kb/ISSUE-LOG.md` row 872
