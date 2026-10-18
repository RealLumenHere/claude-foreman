# wq#4263 — verify wq#4261: kv-secret sha256:ae971efadf59 rotation verdict

**Verdict: CONFIRM**

Author: claude-opus-5-5 (wq#4261, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2178,
merged as `0f6e14f8` → main)
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass)

## What wq#4261 claimed

`mask_replay.py 4260` flagged two kv-secret values from wq#4260's attempt-1 run
(prefix `ANTHROPIC_API_KEY=`, lengths 16 and 20) as BURNED: sha256:ae971efadf59
(this ticket's target) and sha256:f9bfdede01af. Root cause: wq#4260 (installing
the wq#4258 fleet-consolidation litellm snippet) built throwaway test inputs for
`install.sh` under its own `/tmp/fworker-wq4260` — a `printf` into `"$TMPDIR/env"`
and a `Write` of `t1/env` — each giving `ANTHROPIC_API_KEY=` a self-labelled dummy
body (lowercase words, no `sk-ant-` prefix, 16/20 chars against ~108 for a real
key). Same leak shape as row 872: an unsplit `NAME_KEY=` dummy typed into the
worker's own tool_use. Fix: no rotation (neither is a credential); pin both values
as committed text in `harness/tests/fixtures/wq4260_values.py` +
`harness/tests/test_mask_replay_wq4260.py` so future `mask_replay` runs read them
silently.

## What I checked

- **Diff scope** (`git show --stat 0f6e14f8`): two new files only —
  `harness/tests/fixtures/wq4260_values.py` (24 lines) and
  `harness/tests/test_mask_replay_wq4260.py` (37 lines). No changes to
  `masking.py` or `mask_replay.py` — zero blast radius to the masking rules
  other callers depend on. `HEAD`, `main`, and `origin/main` all resolve to
  `0f6e14f8`, confirming this is exactly the merged PR content.
- **Independent live replay, outside the test harness**: ran
  `python3 mask_replay.py 4260` directly. Output: "2 credential-shaped values
  ... 0 unaccounted", exit 0, both shown as `accounted: harness/tests/fixtures/
  wq4260_values.py:19` and `:21`. Matches the PR's claim via a path that
  doesn't go through the pinned pytest at all.
- **Source-context verification, without printing the values**: cross-checked
  the two hashes against `_runs/wq4260/mask-hits-1.json` and
  `_runs/wq4260/loop-handoff-attempt-1.md`. `ae971efadf59` (len 16) is recorded
  at `mask-hits-1.json` with context `printf 'ANTHROPIC_API_KEY=<MASKED>\n' >
  "$TMPDIR/env"` — exactly the throwaway `install.sh` test fixture the PR
  describes. `f9bfdede01af` (len 20) isn't in that hits file (mask_replay scans
  the full transcript, not just logged hits) but `loop-handoff-attempt-1.md`
  independently records `Write: /tmp/fworker-wq4260/t1/env` as the immediately
  preceding tool call — matching the PR's second claimed source exactly. Both
  are self-authored test scaffolding for `install.sh`, not real credentials.
- **Ran the shipped tests**: `pytest tests/test_mask_replay_wq4260.py -v` →
  2/2 pass, including `test_wq4260_corpus_burned_nothing`, which is **not**
  skipped on this host (wq#4260's run dir exists here) — it scores the live
  transcript corpus, not just the fixture.
- **Falsifiability check (wq#2630)**: edited the pinned sha in
  `wq4260_values.py` from `ae971efadf59` to `000000000000` (via the Edit tool,
  not a heredoc) and reran — both tests failed as expected
  (`AssertionError: ['000000000000']`). Reverted with Edit; `git diff`/`git
  status` on the file confirmed clean afterward. The probe can fail; it isn't
  tautological.
- **No row-collision risk**: kb/ISSUE-LOG.md row 1432 is already filed for
  this exact hash/ticket pair with status SOLVED, consistent with the PR.
- **Full-suite regression check**: `pytest tests/test_mask_replay.py -q` →
  97 passed, 1 failed, 25 skipped (268s). The one failure
  (`test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch`, sha
  `9f656ba515b4`) is the exact pre-existing drift already logged at
  kb/ISSUE-LOG.md row 1430 (filed during wq#4255's verify of wq#4251,
  status OPEN) — same test, same sha, unrelated to this diff. wq4261's diff
  touches only the two new wq4260 files; it did not cause and does not fix
  this pre-existing failure.

## Conclusion

The diff is additive-only (two test files, no rule changes), both pinned
hashes trace to independently-verifiable throwaway test scaffolding in
wq#4260's own run transcript (not credentials), the shipped test passes
against the live (unskipped) corpus, a direct `mask_replay.py 4260` run
confirms "0 unaccounted" outside the test harness, and the acceptance probe
demonstrably fails under a deliberately wrong pin. The one full-suite failure
is pre-existing, already logged, and outside this diff's blast radius. No
contradiction found; no escalation to opus-5@high needed.
