# wq#1554 — Langfuse lag monitor: systemd wiring (repointed wq#3124)

## Problem

wq#1552 shipped `harness/langfuse_lag_monitor.py` polling
`/api/public/metrics/export-lag` — an endpoint guessed from a version string
that does not exist in Langfuse v3 or v4, so `fetch_lag_seconds` always
returned None and the monitor could never alert (found in wq#1555). wq#1554
added the `.service`/`.timer` pair but the timer was never installed. wq#3124
repoints the monitor at a signal probed live and installs the wiring.

## Design decision (wq#3124, probed 2026-09-29 against live v4.32.0)

**Lag = canary staleness**: each pass ingests a canary trace via
`POST /api/public/ingestion` and verifies the previous pass's canary via
`GET /api/public/traces/{id}`. Staleness is seconds since a canary was last
confirmed end-to-end (ingestion → queue → worker → ClickHouse → read API);
`LANGFUSE_LAG_THRESHOLD_S` (default 2700s = 3 poll intervals) pages via
`ntfy-send` when exceeded. State (pending canary id, last-confirmed time)
persists at `LANGFUSE_LAG_STATE` (default
`/home/lumen/.local/state/langfuse_lag_monitor.json`).

Live probe results that pin this design (harness probe, 2026-09-29):
`/api/public/health` → 200 `version=4.32.0`; canary ingest → 202;
`GET /api/public/traces/{id}` → 404 at +0s/+5s, **200 at +10s**. The v3→v4
upgrade guide's "GET /api/public/traces/:id returns 404" table does not apply
to this deployment.

Rejected alternative — worker `:3030/api/health?failIfQueueConsumptionStuck=true`:

- the worker port is unpublished and bound to the container IP (172.18.0.x),
  so every probe needs `docker exec` + IP discovery from `docker inspect` —
  fragile, and unreachable from build-worker sandboxes;
- `queueConsumption.secondsSinceLastActivity` conflates an idle fleet with a
  stuck queue, and `failIfEventPropagationStuck` reports `enabled:false` in
  legacy mode, so neither would have caught the 2026-09-28 LiteLLM trace drop
  (wq#3122). Canary staleness catches it by construction: the monitor cannot
  be green while ingestion is dead, because a failing POST stops `last_ok_at`
  from advancing and staleness pages at threshold.

Canary volume: one `langfuse-lag-monitor-canary` trace per 15 min ≈ 96/day,
filterable by the `lag-canary` tag; covered by the wq#1621 retention policy.

## What this installs

`langfuse-lag-monitor.service` runs `harness/langfuse_lag_monitor.py` once,
directly against the `/home/lumen/foreman` PRIMARY tree (same convention as
`ops/wq900-primary-tree-guard`) — no separate deploy copy, so whatever is on
foreman's `main` branch is what executes. `langfuse-lag-monitor.timer` fires
it 5 minutes after boot, then every 15 minutes.

`run()` (`harness/langfuse_lag_monitor.py`) returns exit 1 on a deliberate
skip (no creds at `LANGFUSE_ENV`, or neither read-back nor ingestion reached
the server) — the module's "log-only degradation, never load-bearing"
contract, same as `langfuse_client.load_creds`. `SuccessExitStatus=0 1` in
the service unit reflects that: a skip is not a probe failure and must not
trip a restart loop. Note an alert can still fire on a skip pass: staleness
is measured from persisted state, so a fully unreachable Langfuse pages once
staleness crosses threshold — that is the point of the redesign.

## Install

```
lumen-run --root /home/lumen/foreman/ops/wq1554-langfuse-lag-monitor/install.sh
```

Idempotent, safe to re-run after every `git pull` in foreman. Verify without
changing anything:

```
/home/lumen/foreman/ops/wq1554-langfuse-lag-monitor/install.sh --verify
```
