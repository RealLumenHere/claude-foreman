# wq#1380 — verify-pass: wq#1372 auto-reprove preflight on settings change (PR#264)

**Verdict: CONTRADICT** (escalated to opus-5@high per protocol; confirmed and strengthened
independently) — the PR's own stated invariant, "bound retry cadence so a persistently
broken profile can't re-spawn that session every dispatch pass forever," does not hold
when `harness_preflight.preflight()` raises instead of returning a clean `PreflightResult`.
The exception path bypasses the cooldown ledger entirely, unwinds the whole per-ticket
dispatch loop (not just the offending repo), and repeats every pass indefinitely — the
exact failure mode the cooldown exists to prevent. Everything else in the diff — the clean
reprove-and-spawn path, the clean reprove-fails-and-alerts path, and the cooldown on a
*clean* repeated failure — holds up exactly as claimed and is covered by the PR's own tests.

Commit: `85afbcb` on `main` (merged; `feat/wq1372-auto-reprove-preflight-on-settings-chang-a1`
also carries an earlier duplicate `c730b12` of the same diff, unmerged — not in scope, `85afbcb`
is what's live). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/264. Files: 64
lines in `dispatcher/foreman_dispatcher.py` (`handle_stale_preflight()` +
`PREFLIGHT_REPROVE_COOLDOWN_H` + `preflight_ntfy` state ledger), 70 lines of new tests in
`dispatcher/tests/test_dispatcher.py`, 23-line `harness/README.md` doc addition.

## What the diff does

Before this PR, `foreman_dispatcher.py` stage 9b called `harness_preflight.dispatch_gate_check(repo)`
(cheap, offline, hash comparison against a stamp file) before every spawn; a stale/missing
stamp meant `log.skip(..., "no_fresh_stamp", ...)` and `continue` — forever, until a human
noticed and manually re-ran `harness-preflight <repo>`. This PR adds `handle_stale_preflight()`:
on a gate refusal, call `harness_preflight.preflight(repo)` inline (a real throwaway
`claude -p ... --output-format stream-json` subprocess, not free). Clean reprove → fresh
stamp written, ticket falls through to spawn in the same pass. Failing reprove → `ntfy`
alert, `state["preflight_ntfy"][name] = now` recorded, ticket skipped as `reprove_failed`.
A `PREFLIGHT_REPROVE_COOLDOWN_H = 1` hour cooldown, keyed off that same ledger entry, stops
a persistently broken profile from re-spawning the live-proof subprocess on every
subsequent pass.

## Verification performed

1. Read the diff cold (`git show 85afbcb`) against `harness/harness_preflight.py`'s actual
   `preflight()`/`dispatch_gate_check()`/`PreflightResult`/`run_live_proof()` to confirm the
   new code calls real, matching signatures — it does.
2. Checked out `origin/main`'s `dispatcher/foreman_dispatcher.py`,
   `dispatcher/tests/test_dispatcher.py`, `harness/harness_preflight.py`, `harness/README.md`
   into this verify worktree (scratch only, reverted after) and ran the shipped suite:
   `python3 -m pytest dispatcher/tests/test_dispatcher.py -k preflight -v` → 3/3 passed
   (`test_stale_preflight_auto_reproves_and_spawns`,
   `test_stale_preflight_reprove_failure_alerts_instead_of_silent_skip`,
   `test_stale_preflight_reprove_cooldown_avoids_repeat_live_proof`). Full suite:
   `python3 -m pytest dispatcher/tests/test_dispatcher.py -v` → 65/65 passed, no regressions.
3. Adversarial probe: `harness_preflight._default_runner()` passes `timeout=LIVE_PROOF_TIMEOUT`
   (300s) straight to `subprocess.run()` with no exception handling, and `handle_stale_preflight()`
   calls `harness_preflight.preflight(repo)` with no try/except either — a `TimeoutExpired`
   (or `FileNotFoundError` if `claude_bin` isn't resolvable, or any other `OSError`) has
   nowhere to land except the pass-level catch-all in `run_pass()`. Wrote
   `test_stale_preflight_live_proof_exception_bypasses_cooldown` (monkeypatches
   `harness_preflight.preflight` to raise `subprocess.TimeoutExpired`) and ran it against the
   merged code — passed, confirming the reproduction below.
4. Escalated to opus-5@high per the ticket's contradiction-escalation protocol (fresh
   instance, no shared context beyond the write-up). It independently re-derived the same
   three code locations from `origin/main` and returned **CONFIRMED — real defect, fix it**,
   adding two points I hadn't weighted as heavily: (a) `FileNotFoundError` is *more* likely
   than `TimeoutExpired` in practice — `preflight()` defaults `claude_bin="claude"` resolved
   via `PATH`, and a cron/systemd-launched dispatcher with a minimal `PATH` would hit this on
   the very first auto-reprove, no broken profile required; (b) every *other* `subprocess.run`
   call in `harness_preflight.py` (lines 128, 321, 330, 338, 482) is already wrapped in
   `except (OSError, subprocess.TimeoutExpired)` — `_default_runner` is the sole unguarded
   one, so the gap is an inconsistency with this module's own established pattern, not a
   novel judgment call.
5. Reverted the verify worktree to a clean state (`git checkout HEAD --
   dispatcher/foreman_dispatcher.py dispatcher/tests/test_dispatcher.py harness/README.md
   harness/harness_preflight.py`); no code changes land from this ticket, per its "do NOT
   re-implement; verify" scope. The fix itself is filed separately as wq#1384.

## What holds up

- Clean reprove → fresh stamp → same-pass spawn: exactly as claimed, test passes.
- Clean reprove-fails → `ntfy` alert with the specific "preflight auto-reprove failed for
  `<repo>`: ..." message, skip reason `reprove_failed`: exactly as claimed, test passes.
- Repeated *clean* failures within the cooldown window: no re-spawn of the live-proof
  subprocess, no re-alert, skip reason reverts to `no_fresh_stamp`: exactly as claimed, test
  passes.
- `load_state()`/`save_state()` plumbing for the new `preflight_ntfy` ledger (day-rollover
  handling, `_prune_dedup` reuse at `FIRE_NTFY_DEDUP_H`): consistent with the existing
  `fire_ntfy`/`pacing_ntfy`/`runaway_ntfy` ledgers in the same file, no bugs found.
- `harness/README.md` addition accurately describes the clean-path and clean-failure-path
  behavior (it does not claim anything about the exception path either way).

## Contradiction — live-proof exceptions bypass the cooldown and abort the whole pass

`handle_stale_preflight()` (`dispatcher/foreman_dispatcher.py:584`):

```python
result = harness_preflight.preflight(repo)          # no try/except
if result.ok:
    ...
problems = "; ".join(result.static_failures + result.denials) or result.reason
ntfy(...)
state["preflight_ntfy"][name] = now.isoformat(timespec="seconds")   # never reached on raise
save_state(state)
```

`harness_preflight._default_runner()` (`harness/harness_preflight.py:232`):

```python
def _default_runner(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=LIVE_PROOF_TIMEOUT)   # no try/except
    ...
```

If this raises (`subprocess.TimeoutExpired` on a hung live-proof session — ordinary given a
whole nested `claude -p` session against a possibly-misconfigured settings file and a 300s
timeout; or `FileNotFoundError`/other `OSError` if `claude_bin` isn't resolvable), the
exception propagates through `handle_stale_preflight()` and stage 9b of `_do_pass()`'s
per-ticket loop (no local `try/except` there, unlike stage 7's
`except concurrency.LumenRunError` around `concurrency.list_running_units()`) up to
`run_pass()`'s top-level `except Exception as e: ntfy(f"dispatcher: pass {pass_id} crashed:
{e!r}"); rc = 1`.

Confirmed via `test_stale_preflight_live_proof_exception_bypasses_cooldown`
(`dispatcher/tests/test_dispatcher.py`, written for this verify pass, run against the merged
code and then reverted — not part of the shipped diff):

1. **Pass 1** (`preflight()` raises `TimeoutExpired`): `rc == 1`; one `stage="pass"` error
   logged; one generic `"dispatcher: pass ... crashed: ..."` ntfy fires (not the specific
   "preflight auto-reprove failed for `<repo>`" alert this PR's UX designed for exactly this
   scenario); the triggering ticket never spawns (expected — the whole pass aborted); every
   *other* candidate that pass — unrelated repos, unrelated lanes — is never evaluated
   either, because the exception unwinds past the entire per-ticket loop, not just this one
   repo's tickets.
2. **Pass 2** (immediately after, simulating the next cron tick): `preflight()` is called
   again — the cooldown never engaged, because `state["preflight_ntfy"]` was never written on
   the exception path. A second real live-proof subprocess spawn happens, and the pass
   crashes again. This repeats every pass, indefinitely, for exactly the case the PR's own
   inline comment says it exists to prevent: *"Bound retry cadence so a persistently broken
   profile can't re-spawn that session every dispatch pass forever."*

Impact: conditional on a live-proof subprocess actually raising (requires either a hung
session past 300s or a `claude` binary the dispatcher's environment can't resolve) — not the
common case, but per opus-5's read, more reachable than it first appears (a minimal cron
`PATH` alone would trigger it on the very first auto-reprove attempt, no broken repo profile
needed). Blast radius when it does fire is worse than a single stranded lane: it's the whole
dispatch pass, repeated every pass, with no cooldown backstop — the dispatcher doesn't die
(the pass-level catch-all prevents that) and Frank does get *an* alert each time, but the
cooldown/dedup mechanism this PR exists to add does not hold for this path.

Fix filed as wq#1384 (deps on wq#1372, not blocking further work on this repo): wrap
`run_live_proof()`'s call inside `preflight()` in `except (OSError,
subprocess.TimeoutExpired)` → return a failing `PreflightResult` (matches every other
`subprocess.run` call already in `harness_preflight.py`, and fixes CLI callers of
`preflight()` too, not just the dispatcher); plus defense-in-depth `try/except Exception`
around the `preflight()` call in `handle_stale_preflight()` itself, treated identically to a
clean `ok=False` (alert, write the cooldown ledger entry, save state, skip).
