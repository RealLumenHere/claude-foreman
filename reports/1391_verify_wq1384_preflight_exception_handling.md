# wq#1391 — verify-pass: wq#1384 preflight live-proof exceptions bypass reprove cooldown (PR#270)

**Verdict: CONFIRM** — the diff fixes exactly the contradiction wq#1380 found (confirmed by
opus-5@high escalation) against wq#1372/PR#264: an exception raised out of
`harness_preflight.preflight()` no longer propagates past `handle_stale_preflight()` and
aborts the whole dispatch pass. It's now caught at both the layer that produces it
(`preflight()` itself, matching every other `subprocess.run` call in the module) and, as
defense in depth, at the dispatcher call site. The cooldown ledger (`preflight_ntfy`) is
written on the exception path exactly as it is on a clean `ok=False`, so
`PREFLIGHT_REPROVE_COOLDOWN_H` engages and a persistently broken profile can't re-spawn the
live-proof subprocess every pass — the failure mode both the original PR's comment and
wq#1380's contradiction were about.

Commit: `bf54250`/merge `2b8c958` on `main` (`dispatcher/foreman_dispatcher.py` +6/-1,
`harness/harness_preflight.py` +8/-2, 30 lines of new dispatcher test) plus follow-up
`48d1723`/merge `f08b737` (`harness/tests/test_harness_preflight.py`, +19 lines, direct
coverage of `preflight()`'s own exception handling — filed by the ticket as PR#274, squash
per ISSUE-LOG#130). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/270 (merged).

## What the diff does

1. `harness_preflight.preflight()` (`harness/harness_preflight.py:294-299`): wraps the
   `run_live_proof()` call in `except (OSError, subprocess.TimeoutExpired) as e:` → returns
   `PreflightResult(repo_path, ok=False, reason=f"live proof errored: {e!r}", ...)` instead of
   letting `_default_runner()`'s unguarded `subprocess.run(..., timeout=LIVE_PROOF_TIMEOUT)`
   raise past it. This is the layer where the exception actually originates, and it now
   matches the pattern already used by every other `subprocess.run` in this module.
2. `handle_stale_preflight()` (`dispatcher/foreman_dispatcher.py:620-624`): wraps
   `harness_preflight.preflight(repo)` in `try/except Exception as e:` → builds a matching
   failing `PreflightResult` inline. Defense in depth — even if some future exception type
   preflight() itself doesn't catch, it's absorbed here and treated identically to a clean
   `ok=False` (ntfy alert, `state["preflight_ntfy"][name] = now`, `save_state`, skip).

## Verification performed

1. **Read the diff cold** (`git diff 3d0fe70..e4504dd -- dispatcher/foreman_dispatcher.py
   harness/harness_preflight.py`) against the actual `PreflightResult.__init__(self,
   repo_path, ok, reason, static_failures, denials, digest=None)` signature — the
   dispatcher's manually-constructed fallback `PreflightResult(repo, ok=False, reason=...,
   static_failures=[], denials=[])` matches positionally and by keyword; `digest` correctly
   left at its `None` default. No `TypeError` risk.
2. **Confirmed `subprocess` is imported** in `harness_preflight.py` (line 36) — the new
   `except (OSError, subprocess.TimeoutExpired)` reference resolves.
3. **Traced exception origin**: `run_live_proof()` (`harness_preflight.py:247-255`) calls
   `runner(cmd, repo_path)` with no local guard, and `_default_runner` (line 232) does the
   unguarded `subprocess.run(..., timeout=LIVE_PROOF_TIMEOUT)` — confirms the new `try/except`
   around the `run_live_proof()` call in `preflight()` actually sits in the exception's path
   (not a no-op wrapper around a call that couldn't raise there).
4. **Traced call site**: `_do_pass()` stage 9b (`foreman_dispatcher.py:891-895`) calls
   `handle_stale_preflight()` per-ticket inside the main dispatch loop and `continue`s on a
   `False` return — a caught-and-converted exception now behaves exactly like a normal
   reprove failure from this loop's perspective, so it no longer unwinds past other
   tickets/repos in the same pass.
5. **Ran the shipped suite against `origin/main`** (this verify worktree's branch had gone
   stale relative to `origin/main` mid-verification — see gap note below):
   `python3 -m pytest dispatcher/tests/test_dispatcher.py harness/tests/test_harness_preflight.py -q`
   → 75 passed (matches the ticket's "65+10 tests pass" claim).
6. **Ran the two new tests individually**:
   `test_stale_preflight_live_proof_exception_bypasses_cooldown` (dispatcher — monkeypatches
   `harness_preflight.preflight` to raise `subprocess.TimeoutExpired`, asserts `run_pass() ==
   0`, exactly one ntfy, and pass 2 doesn't re-invoke `preflight()` — cooldown holds) and
   `test_live_proof_filenotfound_returns_ok_false_instead_of_raising` (harness — a `runner`
   that raises `FileNotFoundError`, asserts `preflight()` returns `ok=False` with "live proof
   errored" in `result.reason` instead of raising) — both pass.
7. **Adversarial**: checked `state["preflight_ntfy"]` is initialized to `{}` in fresh state
   (`foreman_dispatcher.py:272`) so `.get(name)`/`.pop(name, None)`/`[name] = ...` in
   `handle_stale_preflight()` can't `KeyError` on a cold ledger; checked no other production
   caller of `harness_preflight.preflight()` exists outside `handle_stale_preflight()` and the
   module's own CLI entrypoint (grep across the repo) — nothing else needed a matching guard.

## Gap noted, not a contradiction of this diff

This verify worktree's branch (`feat/wq1391-...`) was cut from `main` at `dc287e2`, which
turned out to be an *ancestor* of `origin/main`'s current tip (`f08b737` — the PR#274 test
commit landed on `origin/main` after this worktree's branch point). On-disk,
`harness/tests/test_harness_preflight.py` in this worktree was one commit behind and missing
`test_live_proof_filenotfound_returns_ok_false_instead_of_raising`; `dispatcher/foreman_dispatcher.py`
and `harness/harness_preflight.py` were byte-identical to `origin/main` (`git diff HEAD
origin/main -- <those two files>` empty), so the actual fix under test was unaffected. Ran the
missing test directly via `git show origin/main:harness/tests/test_harness_preflight.py` into
a scratch file (removed afterward, `git status --porcelain` clean) — passed. Not a defect in
PR#270/PR#274; just this worktree's branch-point staleness, noted for the record.

## Conclusion

The fix closes wq#1380's contradiction exactly as designed, at both the origin of the
exception and the call site, with tests exercising both layers against the real merged code.
CONFIRM.
