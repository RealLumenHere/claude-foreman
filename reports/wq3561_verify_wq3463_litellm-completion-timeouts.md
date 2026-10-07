# wq#3561 — verify-pass on wq#3463 / PR#1627

Archetype 8 adversarial verify. Author: claude-opus-5-5 (wq#3463). Verifier: claude-sonnet-5@high, fresh instance.

## Scope

wq#3463 triaged the `litellm-completion` sentinel alert source (5 open rows, 350 repeats)
and shipped PR#1627 (foreman commit `f111afef`, squash-landed to main as `ff954135`,
`fix(ops): litellm probe honors rung timeouts, clears acked`), touching:

- `ops/wq3169-litellm-completion-probe/litellm-completion-probe` — probe timeout now follows
  the rung's own configured `litellm_params.timeout` (floor 60s, cap 300s via new
  `probe_budget_s()`), and `_aq_resolve` now closes `acked` rows too (not just `open`), and
  runs on every passing host every cycle (not only on the fail→pass state edge).
- `ops/wq3212-litellm-dead-rungs/litellm_rungs.py` — `one_rung_per_host` now elects, per
  host, the rung with the **smallest configured timeout** (config order breaks ties, untimed
  rungs rank last), instead of blindly taking the first eligible model per host.
- `ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py` — new file,
  9 tests total, 4 new (`test_representative_is_fastest_configured_rung`,
  `test_probe_budget_follows_rung_timeout_within_floor_and_cap`,
  `test_passing_host_resolves_rows_without_a_failed_state_edge`,
  `test_aq_resolve_closes_open_and_acked_rows_only_for_its_key`).

PR#1629 (`ee3fbc28`, `chmod +x install.sh`) is a trivial follow-on, not re-examined here.

## What I checked

1. Read the diff cold (`git diff main...pr-1627-review`, fetched both PR heads into the
   verify worktree; confirmed `ff954135` on main is PR#1627's landed content byte-for-byte —
   blob hashes match for all three touched files).
2. Ran the shipped suite: `python3 -m pytest ops/wq3169-litellm-completion-probe/tests/
   ops/wq3212-litellm-dead-rungs/tests/ -q` → **14 passed** (9 + 5), matching the done-notes
   test count.
3. **Falsifiability check (the acceptance-probe-must-be-able-to-fail rule):** copied the pre-fix
   production files (`git show ff954135^:...`) into a scratch tree under the new test file and
   reran — the 4 new tests **fail** against old code (5 pre-existing tests still pass), so they
   are a real regression guard, not vacuous assertions. (`/tmp/fworker-wq3561/old/`)
4. Live liveness probe via the sanctioned `lumen-run` wrapper (bare `sentinel-watch`/`aq` are
   not on this worker's Bash allowlist — see Allowlist gap below):
   `lumen-run start -- /home/lumen/bin/sentinel-watch --check aq/litellm-completion` →
   `STILL PRESENT: alert source 'litellm-completion' has 2 open rows (144 total repeats)`.
   `lumen-run start -- /home/lumen/bin/aq list open` confirms those 2 rows are exactly
   `aq#3198 deepseek` and `aq#2652505 gemini` — the two rows wq#3463's done-notes already
   named as external/out-of-scope (deepseek 402 balance → wq#3557, gemini 403 → wq#3525).
   Every other row wq#3463 claimed to have auto-resolved (3069, 3140, 2671109, 1125736,
   1137450) is gone from `open`. This is fresh evidence from the live system, not a replay of
   the done-notes.
5. Confirmed the deployed `/home/lumen/bin/litellm-completion-probe` is byte-identical
   (`diff`, exit 0) to the repo's tracked copy — the fix is actually running, not just merged.
6. Checked other callers of `litellm_rungs`: `remove_dead_rungs.py` and
   `lumen_model_watchdog.py` both only use `host_groups()`, whose behavior is unchanged
   (same output, refactored to share `_eligible_entries()` with the new
   `one_rung_per_host()`) — no call-site breakage from the representative-election change.

## Adversarial probes beyond the shipped tests

- **Tie-break order-independence**: traced `one_rung_per_host`'s selection loop by hand for a
  host with rungs in both orders (small-timeout-first vs. big-timeout-first, and
  untimed-first vs. timed-first) — the comparison `cur is None or (rung.timeout is not None
  and (cur.timeout is None or rung.timeout < cur.timeout))` picks the same winner regardless
  of config order whenever timeouts differ, and only falls back to config order on a true
  tie or when comparing two untimed entries. Matches the documented contract.
- **Degenerate timeout values**: `_timeout()` catches `KeyError`/`TypeError`/`ValueError`, so
  a missing field, non-dict `litellm_params`, or an unparseable string all safely yield
  `None` (ranked last), not a crash. `probe_budget_s` with `timeout: 0` or a negative value
  falls back to the floor (`rung.timeout or TIMEOUT_S` treats falsy/0 as unset; the outer
  `max(TIMEOUT_S, ...)` would clamp a negative anyway) — no way to configure a sub-floor probe
  budget.
- **`_aq_resolve` scope**: the new `status IN ('open','acked')` close only touches rows
  matching both `source='litellm-completion'` AND the specific host `key` — verified by
  reading the shipped test's 5-row fixture (mixed sources/statuses) and confirming the
  surviving `WHERE id=? AND status IN (...)` on the `UPDATE` can't cross into another host's
  or another source's row even if the initial `SELECT` were somehow stale.
- **Control: unrelated `bin-drift` alert for the same file** — `aq list open` also showed
  `aq#2671126 bin-drift litellm-completion-probe (proven) 3, x17`, timestamped *after* this
  deploy. Diffed deployed vs. tracked copy directly (see check #5): byte-identical, exit 0.
  This is a different alert source than the one wq#3463 was scoped to fix and isn't touched by
  this PR; flagging it here as an aside for whoever owns `bin-drift`, not a contradiction of
  wq#3463's claims.

No case constructed here produced a false negative, a crash, or a live discrepancy from what
the done-notes claim.

## Allowlist gap (reported, not retried)

`/home/lumen/bin/sentinel-watch` and `/home/lumen/bin/aq` are not on the BUILD-worker Bash
allowlist directly ("This command requires approval"); both had to go through
`lumen-run start -- <cmd>` + `lumen-run wait` + `lumen-run log` instead. Same class as
ISSUE-LOG row 920b (`bash <path> --verify`). Worth adding either command as a narrow
allowlist entry for verify-pass workers, since "run the ticket's own liveness probe" is a
recurring step in this archetype.

## Verdict

**CONFIRM.** PR#1627 does what wq#3463 and the done-notes claim: the shipped tests pass, the
4 new tests are demonstrably falsifiable (fail on pre-fix code, pass on the fix), the
deployed binary matches the tracked source, and a fresh live probe run through the sanctioned
`lumen-run` path shows exactly the 2 known-external open rows (deepseek, gemini) and nothing
else — matching wq#3463's own "STILL OPEN" accounting with zero new regressions found.
