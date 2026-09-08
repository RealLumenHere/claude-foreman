# wq#1554 — Langfuse export-lag monitor: systemd wiring

## Problem

wq#1552 shipped `harness/langfuse_lag_monitor.py` with 7 passing unit tests, but
nothing scheduled, imported, or otherwise invoked it outside its own test file —
wq#1553's verify pass (`foreman/reports/1553_verify_wq1552_langfuse_export_lag.md`)
confirmed it was dead code on the fleet. This ticket adds the missing
`.service`/`.timer` pair, following the established convention
(`ops/wq1373-cf-token-liveness`, `ops/wq900-primary-tree-guard`) of a paired
oneshot service + timer per "poll something, alert via ntfy" monitor.

## What this adds

`langfuse-lag-monitor.service` runs `harness/langfuse_lag_monitor.py` once,
directly against the `/home/lumen/foreman` PRIMARY tree (same convention as
`ops/wq900-primary-tree-guard`, which runs `primary_tree_guard.py` the same
way) — no separate deploy copy, so whatever is on foreman's `main` branch is
what executes. `langfuse-lag-monitor.timer` fires it 5 minutes after boot,
then every 15 minutes.

`run()` (`harness/langfuse_lag_monitor.py:57-68`) returns exit 1 on a
deliberate skip (no creds at `LANGFUSE_ENV`, or the endpoint fetch/parse
failed) — the module's own "log-only degradation, never load-bearing"
contract, same as `langfuse_client.load_creds`. `SuccessExitStatus=0 1` in
the service unit reflects that: a skip is not a probe failure and must not
trip a restart loop.

## Two gaps this ticket could NOT close from this sandbox

wq#1554 also asked to (1) bump the Langfuse containers
(`langfuse/langfuse:3`, `langfuse/langfuse-worker:3`) to v4.32.0 via the
fleet's deploy-safety path, and (2) confirm `LANGFUSE_LAG_ENDPOINT_PATH`
(default `/api/public/metrics/export-lag`) and `LANGFUSE_LAG_FIELD` (default
`lagSeconds`) against the real v4.32.0 API/release docs or a live probe.
Neither was possible from this BUILD-worker session:

- The Langfuse deploy config lives at `/home/lumen/langfuse` (per
  `foreman/reports/opsaudit_a4_langfuse.md:12`, `/home/lumen/langfuse/DEPLOYMENT.md`)
  — outside this worktree's granted directories. A direct `ls` on that path
  is refused by the sandbox: "Claude Code may only list files in the allowed
  working directories for this session." Same structural class as
  `kb/ISSUE-LOG.md` #31/#32/#50 (live-infra/credential-gated work a headless
  build worker cannot reach).
- `WebSearch`/`WebFetch` are listed as available tools but are not on this
  worker profile's allowlist (`grep -i websearch\|webfetch
  harness/worker-settings.json` — no match); calling them raised an
  unapprovable permission prompt with no human watching this session, so
  release-docs verification was not attempted a second time (retrying an
  unapprovable prompt is exactly the loop `kb/ISSUE-LOG.md` #14/wq#1107 warns
  against).
- A live probe against the upgraded instance is unavailable for the same
  reason as the first gap — the instance is not yet bumped, and even a
  bumped instance's admin surface sits behind the same
  credentials/deploy-dir wall.

**`LAG_ENDPOINT_PATH`/`LAG_FIELD` remain unverified guesses** — unchanged
from wq#1552's shipped defaults. Do not treat "monitor ran, no alert fired"
as "lag is healthy" until someone with reach to `/home/lumen/langfuse` (or
outbound web access) confirms the real v4.32.0 shape and, if it differs,
overrides `LANGFUSE_LAG_ENDPOINT_PATH`/`LANGFUSE_LAG_FIELD` via env (no code
change needed either way).

## Install

```
lumen-run --root /home/lumen/foreman/ops/wq1554-langfuse-lag-monitor/install.sh
```

Idempotent, safe to re-run after every `git pull` in foreman.
