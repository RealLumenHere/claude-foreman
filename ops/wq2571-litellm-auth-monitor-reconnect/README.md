# wq#2571 — litellm-auth-monitor exits whenever the LiteLLM container restarts

## Problem (wq#2564 audit finding)

`/home/lumen/bin/litellm-auth-monitor` tails `docker logs --follow litellm`
for auth failures and writes them to the wq#721 audit log
(`/home/lumen/logs/litellm/auth-failures.jsonl`). Its header comment claimed
`docker logs --follow` "reconnects automatically" on container restart — it
does not. The follow stream ends (exit 0) the moment the container stops or
is recreated. With `set -euo pipefail` on line 12, that end-of-pipe killed
the whole script, and `Restart=always` (10s, set by wq#897) respawned it —
`NRestarts=153` observed at the time this ticket was picked up.

Two consequences of the old design, both now fixed:

1. **Data loss.** Every respawn re-ran with a fixed `--since 1m`. Any gap
   between the container stopping and the monitor being respawned that
   exceeded a minute (docker pull, slow container recreate, a burst of
   several bounces) silently dropped every 401 in that window — the exact
   audit-trail hole wq#721 exists to prevent.
2. **Broken JSON on a backslash.** Line 58 built JSONL with
   `printf` + `"${line//\"/\\\"}"`, which escapes only double quotes. A raw
   log line containing a literal backslash (e.g. a URL-encoded path) produced
   an invalid JSON escape, corrupting that line of the audit log.

## Fix

`litellm-auth-monitor` now owns its own reconnect loop instead of relying on
`Restart=always` to notice a dead stream:

- `run()` wraps `docker logs --follow --timestamps --since "$since" ...` in
  an outer `while true`. When the stream ends for any reason, it sleeps with
  exponential backoff (1s → 2s → 4s … capped at `LITELLM_AUTH_MAX_BACKOFF`,
  default 60s) and reconnects — the script process itself never exits on a
  container bounce, so systemd has nothing to restart.
- `--timestamps` gives each line a leading RFC3339Nano timestamp;
  `process_line()` captures it and `save_last_seen()` persists it to
  `STATEFILE` (`$LOGDIR/.monitor-last-seen`). The next `docker logs --since`
  call resumes from that exact timestamp, not a fixed 1-minute window — the
  only gap possible is the backoff sleep itself, not the old worst case
  (respawn latency + missed history).
- `emit_json()` replaces the `printf`/manual-escape JSONL builder with
  `python3 -c`-style `json.dumps`, fed via environment variables (never
  interpolated into the python source), so any character in a log line —
  quotes, backslashes, control characters — round-trips as valid JSON.

The event-matching logic (auth_exception_handler → `auth_failure`,
`401`/`"401"` → `auth_401`, the "Requester IP Address:" lookahead read) is
unchanged from the original, just moved into `process_line()` so it's
callable from both the live reconnect loop and the offline test.

## Test

`test_litellm_auth_monitor.sh` sources the script with
`LITELLM_AUTH_MONITOR_TEST_MODE=1` (skips the `run` main loop) and drives
`process_line()` directly against fixture lines — no docker/container
needed:

```
$ ./test_litellm_auth_monitor.sh
PASS: backslash log line -> valid JSONL
PASS: last-seen timestamp persisted for resume
PASS: stale 'reconnects automatically' claim removed
ALL PASS
```

Also reachable via the worktree's allowlisted test path:
`bash scripts/wq2571_test_litellm_auth_monitor.sh` (thin wrapper to this
file).

Verified 2026-09-25.

## NRestarts flat across a container restart (AC3)

Not exercised live in this sandbox — the worker's docker allowlist covers
only `docker ps`/`docker logs`, not `docker restart`, and this environment
has no permission to bounce the `litellm` container. The mechanism is
structural, not timing-dependent: the only way the old script's process
exited was the `docker logs --follow` pipe ending under `set -euo pipefail`
(line 12/29 of the original). That pipe is now inside `run()`'s
`while true` with no `-e`, so a container restart ends the inner `read`
loop and falls through to the backoff `sleep` — the script (and therefore
`MainPID`) never exits, so `systemctl show ... -p NRestarts` will not
advance. `install.sh` deploys the fix live and restarts the unit once to
pick it up (one expected bump at deploy time); every container restart
after that should leave `NRestarts` unchanged. Confirm post-deploy with:

```
systemctl show lumen-litellm-auth-monitor.service -p NRestarts
# bounce the litellm container
systemctl show lumen-litellm-auth-monitor.service -p NRestarts   # same value
```

## Deploy

`install.sh` — copies the fixed script to `/home/lumen/bin/litellm-auth-monitor`
and restarts `lumen-litellm-auth-monitor.service`. `install.sh --verify` is
read-only (wq#1696 contract): confirms the live script matches this bundle's
copy and that the stale "reconnects automatically" claim is gone.
