# wq#3742 — verify-pass on wq#3736 (litellm-completion-probe probe-key fix)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1751 (merged ad3bf846)
Author: claude-opus-5-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#3742)

## Claim under test

wq#3736: 12 open `litellm-completion` aq rows (40 repeats) were one root cause —
`LITELLM_MASTER_KEY` is retired for non-admin routes (wq#914), so every host
probe got a 401 and `classify()` filed it as "unreachable" per host. Fix:
switch to the model-watchdog service key, preflight `GET /v1/models`, and
collapse a rejected/unreadable key into one `(litellm-completion, probe-key)`
row instead of N per-host rows; host state/rows carry forward untouched.

## What I checked

1. **Read the diff cold** (`git show ad3bf846`): script, tests, README.
   `check_probe_key()` preflights `MODELS_URL` with the service key; a 401/403
   returns a single `probe-key`/`auth` failure dict, anything else (5xx,
   connection error) returns the key and lets the real per-host probes run.
   `main()` on a key failure sets `results = [key_failure]` and unions every
   currently-due rung's host into `skipped`, so `apply_results()`'s
   "host left the probe set" branch (which resolves a row when a host is
   absent from both `probed_hosts` and `skipped`) cannot mistake "we didn't
   check you this round because the key is bad" for "your rung was removed
   from config." Traced this path by hand against `apply_results` — it holds.
2. **Ran the test suite**: `python3 -m pytest .../tests/test_litellm_completion_probe.py`
   → **19 passed**, including the two new ones that target this exact fix
   (`test_check_probe_key_reports_rejection_once`,
   `test_rejected_key_files_one_row_and_leaves_host_rows_alone`) plus the
   updated `test_read_service_key_strips_without_logging`.
3. **Deployed vs repo**: `diff` on `/home/lumen/bin/litellm-completion-probe`
   vs the repo copy — byte-identical.
4. **Ran the actual acceptance probe**: `python3 /home/lumen/bin/sentinel-watch
   --check aq/litellm-completion` → `CLEAR`. `aq list open | grep
   litellm-completion` → no rows, before any live run.
5. **Ran the live script for real** (`python3 /home/lumen/bin/litellm-completion-probe
   --print`, against the production gateway, ~5 min wall time): `probed 13
   hosts (0 skipped), 3 failing: cerebras(unreachable),
   100.80.71.13:11434(unreachable), 100.98.74.87:11434(unreachable)`. The
   service-key preflight succeeded (no `probe-key`/auth row), so this is the
   fixed code correctly attributing 3 *independent* real host failures to 3
   separate rows — the opposite of the bug (one cause, many rows) is not
   being reintroduced here, because these are not one cause. Corroborated
   independently: `model-watchdog` filed its own `model DOWN` rows for
   `cerebras-llama` / `cerebras-oss-120b` / `isis-qwen3-32b` in the same
   window (aq 2671367-69), so cerebras being down is real, not an artifact
   of this script.

## Side effect (disclosed, not reverted)

Running the live probe for verification created 3 new real aq rows
(2671364-66) and sent urgent ntfy pages for them — the timer that would
normally have caught this was disabled 10-08 per wq#3648, so nothing had
probed these hosts in days and my run was the first to notice. These are
genuine host-down conditions (see model-watchdog corroboration above), not
a flaw in the PR under test. Per instinct 3 (full autonomy, record and
report rather than ask), I left them open rather than resolving them — the
pager page is not spurious — triage of the 3 live host failures belongs to
sentinel-watch's/the next worker's queue, not this verify ticket.

## One scope note (non-blocking)

`check_probe_key`'s `except Exception: return key, None` branch means a
*total* gateway outage (connection refused / timeout on `/v1/models`, as
opposed to a 401/403 rejection) still falls through to per-host probing and
would still fan out into N "unreachable" rows — the same amplification
shape as the original bug, just from a different single root cause. This
is outside what wq#3736 claims to fix (its title and root-cause note are
specifically about the retired master key / 401), and a full gateway outage
already has its own alerting per the script's own docstring
(`litellm-auth-alert` wq#911, `health_check_failure_alert` wq#1295,
`lumen-cron-deadman`). Flagging for awareness, not as a contradiction — the
diff does exactly what it says it does for the case it says it fixes.

## Verdict

**CONFIRM.** The diff matches the stated root cause, the fix collapses a
rejected/unreadable probe key into one row while leaving host rows/state
untouched, all 19 tests (3 new/changed) pass, the deployed script matches
the repo, and a live run against the real gateway behaves exactly as
designed. No contradiction — no escalation to judgment.
