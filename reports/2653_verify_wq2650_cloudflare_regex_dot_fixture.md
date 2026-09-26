# wq#2653 — verify of wq#2650 (PR#1016, merged 7984269, main=19c4cd7)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2650 is a rotation row for the mask tripwire's `cloudflare` pattern,
sha256:55e0c019e958, len 33, fired on wq#2638 attempt 1's own
`grep -o -n ".\{60\}<fixture>.\{40\}" kb/ISSUE-LOG.md`. wq#2650's claim: this is not
a live credential — it is wq#2350's synthetic `cfut_...` concatenation fixture
(ISSUE-LOG row 724) with wq#2638's own regex `.` glued on the end, which extends
the match past the mask literal and re-arms `masking._matches()` (ISSUE-LOG row
724 direction (1), still open, owned by wq#2638 — not touched here to avoid a
collision). wq#2650's fix settles the row in ISSUE-LOG #747 and pins the exact
value as a fixture in `harness/tests/test_mask_replay.py`, so `account()` finds
it in committed text and it stops reporting BURNED. The PR diff is one file, one
new test, 26 insertions, no deletions — it does not touch `masking.py` or
`mask_replay.py` production code.

I read the diff cold (`git show` on PR#1016's head commit, `654f8c4`, fetched via
`refs/pull/1016/head` into my own worktree — never into a primary tree), read
`account()`/`_seam_joined()`/`_SEAM` in `harness/mask_replay.py` and
`masking.mask_text()`, and independently recomputed every factual claim rather
than trusting the diff's own assertions.

## Per claim

| wq#2650 claim | Result | Evidence |
|---|---|---|
| The burned value's sha256 is `55e0c019e958`, len 33, and equals `"REALSECRETVALUE123456" "cfut_" "MASKED" "."` (wq#2350 fixture + a bare `.`) | CONFIRMED | Recomputed independently outside the test: `hashlib.sha256(('REALSECRETVALUE123456'+'cfut_'+'MASKED'+'.').encode()).hexdigest()[:12]` → `55e0c019e958`, `len` → `33`. Matches the ticket title's hash exactly. |
| The reconstruction isn't post-hoc fabrication — it traces to wq#2650's own offline investigation | CONFIRMED | `/tmp/fworker-wq2650/probe.py` (pre-existing, untouched by this diff) already assembled the fixture from the same four seam-split literals before this PR existed. Ran it against the current `harness/`: it reproduces a `cloudflare` hit on the mask-plus-dot case and on the full fixture, same shape as before. |
| The underlying `masking.py` dot-re-arm bug is real and still open (deferred to wq#2638, not fixed here) | CONFIRMED | `masking.mask_text('cfut_' + 'MASKED' + '.')` on current `harness/masking.py` → `('cfut_MASKED', ['cloudflare'])` — still fires. This PR's diff touches only the test file, confirmed by `git show --stat` (1 file, +26/-0). |
| The new test's `body` (33 chars) + a prepended `"cfut_"` reconstructs the exact transcript line that fired wq#2638 attempt 1, and `mask_replay.replay()` on that line yields one `cloudflare` record matching `WQ2650_VALUE` | CONFIRMED | `pytest -k wq2650` → 1 passed. Manually re-derived the `line` construction and confirmed it matches ISSUE-LOG row 747's stated 5-segment concatenation. |
| `account()` finds the pinned fixture in committed text and stops flagging it, without weakening `account()`/`masking.py` for any other case | CONFIRMED | `mask_replay.account(body, {"this": here})` (test body, line 1519) passes; full targeted suites (`test_mask_replay.py`, `test_masking.py`) pass except one pre-existing, unrelated failure (below). No change to `account()`/`_seam_joined()`/`masking.py` in the diff. |
| Reading the new test file itself (e.g. a future worker `cat`/`grep`-ing it) cannot re-arm the same alarm | CONFIRMED | Ran `masking.mask_text()` directly against every line of the landed file containing `REALSECRETVALUE` (2 lines: the `body =` assignment and the `for seamed in ...` loop) — both return `[]` (no rule fires). This is exactly what the test's own closing loop asserts, and it holds against the real file, not just the in-test string. |
| wq#2650's own "done" claim — PR merged, `mask_replay 2638` exits 0 with 0 unaccounted | CONFIRMED | `git merge-base --is-ancestor` on the PR's exact head SHA (`654f8c4`) against `origin/main` initially read false — that SHA isn't on `main`; `lumen-pr` landed it as a different commit (`7984269`, message "settle wq2650 as fixture plus regex dot"). Diffed `654f8c4:harness/tests/test_mask_replay.py` against `7984269:` of the same path: the only delta is two unrelated tests from a *later* ticket (wq#2648) inserted earlier in the file — the wq#2650 test itself is byte-identical. Ran the ticket's own designated probe, `python3 /home/lumen/foreman/harness/mask_replay.py 2638`, against the live primary (already synced past `7984269`): exit 0, final line `No rotation: every value is committed text, or a string today's masking.PATTERNS no longer reads as a credential.`, and the specific row is `[tracked] cloudflare len=33 sha256=55e0c019e958 prefix='cfut_' ... accounted: harness/tests/test_mask_replay.py:1513` — the exact line the landed test put there. |

## Additional checks

- `pytest harness/tests/test_mask_replay.py harness/tests/test_masking.py -q` on the
  PR content: 1 failed (`test_wq1819_transcript_burned_nothing`, unrelated), rest
  green. Confirmed the failure is pre-existing and environmental, not caused by
  this diff: reverted just `test_mask_replay.py` to `origin/main`'s pre-PR version
  in a scratch checkout and reran `-k wq1819` — identical failure
  (`assert 3 == 1`, driven by a live snapshot dir under
  `/home/lumen/worktrees/wq1819-a10` that has since accumulated more records than
  the test's fixed expectation; nothing to do with wq#2650/#2653).
- No re-implementation performed, per verify-pass doctrine.

## Verdict

CONFIRM.
