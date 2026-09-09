# AuditForecast (wq#1574)

Transparent forecasting scaffold from arXiv:2609.05905 ("From Narrative to
Auditable Forecasts: A Structured Scaffold for Agentic Forecasting"):
baseline (reference-class) probability -> retrieved evidence -> mechanical,
bounded log-odds updates -> a JSON report where every number traces back to
a prior step.

Code: `audit_forecast.py`. Tests: `test_audit_forecast.py` (24/24 passing,
no network required — `gather_evidence()`'s SearXNG call is exercised
separately, live, since it needs the fleet's SearXNG instance).

```
python3 audit_forecast.py --self-test
python3 audit_forecast.py "Will X happen by 2026-12-31?" --baseline 0.3 \
    --evidence '[{"source": "search result 1", "stance": "support", "strength": 1.5}]'
```

## What's wired, what's not

- **Baseline + odds math**: fully implemented, unit-tested, no external
  dependency. `baseline_probability()` deliberately refuses to invent a
  base rate — the caller must supply one from a reference class.
- **Evidence retrieval**: `gather_evidence()` calls the fleet's SearXNG at
  `127.0.0.1:8888` (JSON API), the documented, reachable integration
  surface from `foreman/research/SEARXNG_FLEET_INTEGRATION.md:34`. Stance
  and strength assignment per evidence item is left to the caller — turning
  a snippet into a stance/strength is a judgment call this scaffold
  deliberately keeps mechanical and out-of-band rather than silently
  automating it with an unverified model call.
- **minerva_verify judging**: `submit_for_judging()` is a stub that always
  returns `judge_unavailable`. minerva_verify.py's actual CLI/import
  surface is outside this worker's sandbox reach (ISSUE-LOG #233, #240) —
  reading it to find the real calling convention needs a session with
  `/home/lumen/minerva` in its allowed dirs. Do not fill this in by
  guessing a flag: the file exists on disk (so a guessed subprocess call
  will look plausible) but a wrong argument shape hangs or fails silently
  against the real script (confirmed while building this module).
- **minerva_factpack retrieval reuse**: not wired for the same reason.
  SearXNG evidence gathering above is a working substitute for now.
- **systemd timer**: `auditforecast.service`/`.timer` are provided but
  **not installed/enabled**. On-demand CLI use covers today's need; wire
  the timer once a real forecast-question source (a queue, a cron list of
  standing questions) exists — building a periodic job with nothing to
  feed it would just be a no-op timer.

## Rollback

Delete `ops/wq1574-auditforecast/`. Nothing else references it; no timer is
enabled, no other pipeline was touched.
