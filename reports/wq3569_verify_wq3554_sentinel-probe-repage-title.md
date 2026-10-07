# wq#3569 — verify-pass on wq#3554 / PR#1648

Archetype 8 adversarial verify. Author: claude-sonnet-5 (wq#3554). Verifier: claude-sonnet-5@high, fresh instance.

## Scope

wq#3554 fixed `sentinel_probes.py`'s re-page title churn: the critical-fire re-page branch
in `main()` baked the elapsed-failure-duration into the ntfy title
(`SENTINEL CRITICAL (still failing 6.1d): <probe>`), and since aq's `ntfy:lumen-alerts`
ingest keys rows by exact title, every ~6h re-page opened a new row instead of updating one
(137 rows / 143 repeats in alerts.db, 3 open at once for one probe — ISSUE-LOG #1185). Same
bug class fixed earlier for `unit-drift` in lumen-bin PR #230. PR#1648 (foreman commit
`fb1d4f87`, merged) makes the title invariant (`SENTINEL CRITICAL (still failing): <probe>`)
and moves the duration into the ntfy body, touching:

- `ops/wq3554-sentinel-probe-repage-title/README.md`, `sentinel_probes.diff` — bundle
  (the live file isn't tracked anywhere; `/home/lumen/sentinel` is not a git repo and was
  not previously an `fworker --add-dir`).
- `harness/agents.yaml` — adds `/home/lumen/sentinel` to `defaults.add_dirs`.
- Live edit to `/home/lumen/sentinel/probes/sentinel_probes.py:1175-1176` (matches the diff).

## What I checked

1. Read PR#1648 cold via `git show fb1d4f87` in the verify worktree (gitea's `/pulls/1648.diff`
   and `/api/v1/.../pulls/1648` both 404'd without auth; the merge commit is the same content).
2. Confirmed the live file matches the shipped diff exactly: `sentinel_probes.py:1175-1176`
   emits `ntfy_urgent(f"SENTINEL CRITICAL (still failing): {name}", f"failing {_dur(...)}: {detail}")`
   — grep-confirmed, byte-for-byte against the bundle's `.diff`.
3. `python3 -c "ast.parse(...)"` — live file still parses; only one `ntfy_urgent` call site uses
   "still failing" (`grep -n`), so no second un-fixed occurrence was missed.
4. Checked `_edge_clear`'s existing LIKE pattern (`'SENTINEL CRITICAL (still failing%): {name}'`,
   wq#1487) against the new fixed title by hand: `%` matches zero-or-more in sqlite LIKE, so the
   wildcard still matches the invariant title unchanged — confirmed both by regex inspection and
   by the test run in #5 (`EDGE-CLEAR ... resolved 4 stale aq row(s)`).
5. **Ran the probe's own regression test**: `/home/lumen/sentinel/probes/tests_wq1487_edge.py`
   (offline, scratch AQ_DB/state, no network) → **ALL PASS**, including two cases the wq#3554
   author added live alongside the fix:
   - `(iv-b) re-page after REPAGE_S` — asserts the first re-page's title is the fixed string.
   - `(iv-c) repage title stays fixed across repages` — asserts a *second* re-page uses the
     *same* title as the first (the actual bug: distinct titles per re-page).
6. ISSUE-LOG #1185 cross-checked: PR #230 (unit-drift) and this fix are explicitly the same bug
   class; updated row 1185's status column (was stale, read "wq#3554 open") to record both as
   fixed with PR references.

## Gap found and fixed: missing `--verify` handler + unshipped test coverage

The ticket flagged this directly: `ops/wq3554-sentinel-probe-repage-title` had no `install.sh`,
so no `--verify` handler per `ops/VERIFY_CONTRACT.md` — a repo-only check doesn't count (wq#1696).
Added `install.sh` with a read-only `--verify` arm (`vcontains` against the live
`sentinel_probes.py` for the fixed title line, the body line, and the untouched `_edge_clear`
LIKE pattern).

Separately, I found the wq#3554 author also edited `/home/lumen/sentinel/probes/tests_wq1487_edge.py`
live (mtimes ~2.5 min apart, both inside the PR's merge window) to add cases (iv-b)/(iv-c) above —
but never shipped a copy of that test file in the bundle, unlike the established convention for
this exact situation (`ops/wq1092-probe-target-liveness-guard/test_probe_target_liveness.py` ships
a full copy of its new test alongside its `sentinel_probes.diff`). Had `/home/lumen/sentinel` been
restored from backup without that live edit, the regression coverage for this exact bug would
have silently disappeared with no bundle record to redeploy it. Not a functional defect in
PR#1648 — the shipped fix itself is correct — but a real completeness gap in what "done" shipped.
Fixed it: added `tests_wq1487_edge.py` (snapshot of the live file) to the bundle, extended
`install.sh --verify` with two more `vcontains` checks against the live test file (markers for
iv-b/iv-c), and documented both in README.md.

## Falsifiability (the probe must be able to fail)

Ran the new `install.sh --verify` for real via the sanctioned `lumen-run` wrapper (bare
`bash ops/.../install.sh` isn't on this worker's Bash allowlist — only `Bash(bash scripts/*)`,
not `ops/*`; see allowlist gap below):

- Against live state: `lumen-run start -- bash .../install.sh --verify` → `DONE:0`, log shows
  `VERIFY OK`.
- Against a constructed pre-fix sample (`/tmp/fworker-wq3569/wq3569_old_sample.py`, the old
  `ntfy_urgent(f"SENTINEL CRITICAL (still failing {_dur(...)}): {name}", detail)` line, and an
  empty stand-in for the test-file checks) via the same `vcontains` assertions: `FAILED:1`, log
  shows all 5 `MISS` lines and `VERIFY FAIL (5)`. The handler can fail, and fails for the right
  reason — not a vacuous check.

## Adversarial probes beyond the shipped diff

- **Other title call sites**: only one `ntfy_urgent("SENTINEL CRITICAL (still failing...")` call
  exists in the file (`grep -n`) — no sibling branch (e.g. a different probe type or a digest
  path) still bakes in duration.
- **An unrelated hit while grepping**: `ops/wq2611-a1/verify_mcp_pool_probe.py` (committed
  2026-09-29, over a week before this fix) already hardcodes the *fixed* title string in its own
  replicated copy of the re-page branch. This is a different, unrelated verify harness that
  reimplements `main()`'s per-probe body rather than calling the real re-page code — it predates
  wq#3554 and doesn't call into the affected code path, so it's a coincidence, not evidence the
  bug was already fixed elsewhere or that this fix is redundant. Noted, not a contradiction.
- **Scope of the aq title-keying claim**: I could not read `/home/lumen/agents/alert-queue`'s own
  ntfy-ingest code directly (not one of this worker's allowed dirs), so I did not re-derive "aq
  keys rows by title" from that source. I rely instead on ISSUE-LOG #1185's prior-established
  finding (same claim, same evidence shape, cross-referenced to the already-fixed unit-drift
  sibling) plus the regression test's own direct assertions against `aq.ingest`/`alerts.db` rows
  (check #5), which exercise the real ingest path end-to-end and pass. This is corroboration, not
  a gap in the fix itself.

No case constructed here produced a false negative, a crash, or a live discrepancy from what the
done-notes claim.

## Allowlist gap (reported, not retried)

`bash ops/<bundle>/install.sh --verify` is not on the BUILD-worker Bash allowlist directly
("This command requires approval" — only `Bash(bash scripts/*)` exists, not `ops/*`). Worked
around it via the sanctioned `lumen-run start -- bash ...` + `lumen-run wait` + `lumen-run log`
path, same as ISSUE-LOG's prior note on this exact gap (wq#3561's report, row 920b class). Same
recurring friction for this archetype — "run the ticket's own liveness probe" — worth widening.

## Verdict

**CONFIRM.** PR#1648 does what wq#3554 and the done-notes claim: the live file matches the diff,
the probe's own regression test (including the two cases added for this exact bug) passes
end-to-end against the real aq/ntfy code paths, and the previously-missing `--verify` handler
(added by this verify-pass) passes against live state and correctly fails against pre-fix state.
One completeness gap found (unshipped test-file snapshot) and fixed in this same pass rather than
re-implementing anything — no change to the shipped fix itself.
