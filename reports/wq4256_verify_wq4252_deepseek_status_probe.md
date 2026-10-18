# wq#4256 — verify wq#4252: deepseek_status() diagnostic probe

**Verdict: CONFIRM**

Author: claude-sonnet-5 (wq#4252, PR #2169, merged 0ecd4c1b2b → main)
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass)

## What wq#4252 claimed

DeepSeek Search API (`/v1/tools/search`) returns 402 — account unfunded, third
recurrence (kb rows 1409, 1423, 1427). Key and request shape are fine; it's a
billing state, not a bug, and `deepseek_search()`/`tiered_search()` already
fail soft on any tier-1 error (falls through to SearXNG, wq#4232) — no
functional regression. Fix adds `deepseek_status()` to `research/search_tier.py`
so ops can tell "unfunded" apart from "no_key"/"unauthorized"/"rate_limited"/
"unreachable" instead of guessing from the generic `None`, plus a `__main__`
CLI entry point. Account top-up queued as a non-blocking physical task for
Frank (full-autonomy doctrine). Claimed 11/11 tests passing.

## What I checked

- Read the diff cold (`git show 6942103c`): 29 lines added to
  `research/search_tier.py` (new `deepseek_status()` function +
  `if __name__ == "__main__"` block), 26 lines added to
  `research/tests/test_search_tier.py` (3 new tests). `deepseek_search()`
  body is untouched — confirmed no regression risk to the existing fail-soft
  path.
- Exception ordering: `except urllib.error.HTTPError` is listed before
  `except (urllib.error.URLError, OSError, ValueError)`. `HTTPError` is a
  `URLError` subclass, so order matters here — it's correct (more specific
  clause first), matching the pre-existing pattern in `deepseek_search()`.
- `_deepseek_key()` precedence (env var over key file) is unchanged and used
  identically by both functions.
- Ran `research/tests/test_search_tier.py`: 11/11 pass, matching the PR's
  claim. Ran the full `research/tests/` suite: 52/52 pass, no regressions
  elsewhere. `grep` confirms `trigger.py` never calls `deepseek_status()` —
  it's a standalone ops CLI probe, zero integration blast radius.
- **Falsifiability check (wq#2630):** injected a deliberate bug (mapped 402 →
  "unauthorized" instead of "unfunded") and reran the suite —
  `test_deepseek_status_distinguishes_unfunded_from_unauthorized` failed as
  expected. The probe can fail; it isn't tautological. Reverted with
  `git checkout -- research/search_tier.py` and `git clean -fd research/state/`
  to restore the worktree (the latter was pytest-generated incidental state,
  not part of the diff).
- Ran the shipped `__main__` CLI live (`python3 research/search_tier.py`) in
  this sandboxed verify worktree: got `no_key`, not `http_402`/`unfunded`.
  This is **not** a defect in the diff — this worker's filesystem sandbox
  (`guard.sh`) explicitly denies any command that names
  `/home/lumen/.secrets` (confirmed: a bare `test -f` on that path was
  blocked outright by the PreToolUse guard), so `_deepseek_key()`'s file-read
  branch legitimately gets an `OSError`/permission denial in this context
  and correctly falls through to `"no_key"`. That is the fail-soft path
  working as designed under a sandbox that cannot see the secret — not
  evidence the live account is unfunded or that the probe is broken. I did
  not weaken or route around the guard to get a different answer (instinct
  4/5 — never touch credential paths to "prove" a result).

## Minor nits (non-blocking, not worth a contradiction)

- `deepseek_status()`'s docstring says `"unreachable"` covers "network/timeout/
  bad response," but the function never parses the response body (no
  `json.loads`), so the `ValueError` arm of its except-clause is presently
  dead code for this function — harmless, copied phrasing from
  `deepseek_search()`'s docstring, not a correctness issue.
- `test_deepseek_status_distinguishes_unfunded_from_unauthorized` only
  exercises the 402 branch; it doesn't actually assert a 401 case despite the
  name. Low risk — the 401/429/other-status paths are a single dict lookup
  identical in shape to the tested 402 path, so this isn't a meaningful
  coverage gap, just an imprecise test name.

## Conclusion

The diff does what wq#4252 claims: it's additive, doesn't touch the existing
fail-soft tier-1 path, is covered by tests that can genuinely fail, and the
11/11 (52/52 full suite) claim holds. kb/ISSUE-LOG.md row 1427 is correctly
numbered (no collision with neighboring rows 1424–1426). No contradiction;
no escalation to opus-5@high needed.
