# wq#3746 — verify-pass of wq#3737 (daily aq hygiene re-hosted on lumen-foreman-health.timer)

Verifier: sweep-f2-1f51b8 (orchestrator session; the dispatched fworker returned FALSE_COMPLETE with no
mechanical signature). Verified 2026-10-12 12:50-13:00Z against origin/main 6959bd3f.
Author PRs: #1752 (c8a4ebfe) + #1756 (6959bd3f).

## Verdict: CONFIRM

## What was checked
1. **Tests, fresh detached worktree at origin/main:** `pytest -q ops/wq3349-sentinel-watch/tests dispatcher/tests/test_dispatch_health_idle.py`
   -> 28 passed (author: 57 targeted incl. aq_daily tests; the two suites run here cover the new sentinel-watch
   `aq/hygiene` finding and the dispatch_health hook).
2. **Deployed == main:** `/home/lumen/bin/sentinel-watch` byte-identical to `ops/wq3349-sentinel-watch/sentinel-watch`;
   `/home/lumen/foreman/dispatcher/dispatch_health.py` and `ops/wq3176-aq-daily-ack-extend/aq_daily_ack_extend.py`
   in the primary tree are identical to main (dispatch_health runs from the primary tree; the script path is
   derived from DISPATCHER_DIR -> `ops/wq3176-aq-daily-ack-extend/aq_daily_ack_extend.py`, present).
3. **The host timer is alive and the hook fired for real:** `lumen-foreman-health.timer` last 12:49:53Z, next 12:55:02Z
   (5-min cadence, Frank-kept). Journal of `lumen-foreman-health.service` 08:43:27Z:
   `aq_daily_ack_extend: 4 row(s) acked (recovered/failed pairs), 32 row(s) acked (stale info-class incl.
   recurring), 18 row(s) acked (per-source display cap)` = 54, matching the ticket. State file
   `/home/lumen/logs/aq-daily-ack.state.json` = `{"last_ok": 1791794607.48}` (08:43:27Z) — the 24h gate is armed.
4. **Probes:** `sentinel-watch --only aq --check aq/hygiene` -> `CLEAR: aq/hygiene` rc 0;
   `sentinel-watch --only aq --check aq/ntfy:lumen-alerts` -> `CLEAR: aq/ntfy:lumen-alerts` rc 0.
   NOTE for future verifiers: without `--only aq` the full sweep exceeds 30s (LiteLLM/probe groups), which is
   what looks like a hang; the quoted probe commands work, they are just slow.
5. **Adversarial read of the dispatch_health hook:** `run_aq_hygiene()` wraps the subprocess in try/except
   (OSError, TimeoutExpired) with a 120s cap and only runs when `not a.dry_run` — it cannot take the watchdog
   down with it, and `--daily` self-gates to once per 24h so the 5-min host cadence does not multiply acks.
   `FOREMAN_AQ_HYGIENE` env override exists for tests. The wq#3648 timer cut is not undone: no new timer unit,
   the kept one hosts the job (freeze-compliant: names what it replaces, lumen-aq-autoack/escalate).

## Residual risk (not a contradiction)
- If `lumen-foreman-health.timer` is ever cut too, hygiene dies with it; `aq/hygiene` (stall >48h) is the
  only alarm, and it lives in sentinel-watch, which runs on its own kept timer. Acceptable single-hop coupling.
