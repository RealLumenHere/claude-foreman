# wq#1698 — stop supergateway body logs from reaching journald/Bes

## Problem (verified 2026-09-10/11)

`desktop-commander-mcp.service` runs supergateway v3.4.3 with no `--logLevel`
flag, so it defaults to `info` (`/home/lumen/.npm-global/lib/node_modules/supergateway/dist/index.js:86-90`).
At `info`, every MCP request/response body is logged verbatim:
`dist/gateways/stdioToStatelessStreamableHttp.js:194` (`Child → StreamableHttp:`),
`:207`/`:244` (`StreamableHttp → Child (…):`), `:235` (`StreamableHttp → Child:`).
systemd's default `StandardOutput=journal`/`StandardError=journal` puts all of
it in the journal, and `vlogs-forwarder` ships the journal to VictoriaLogs on
Bes (90d retention). Two consequences, both confirmed:

1. **Retention**: ~774k supergateway lines/day vs `SystemMaxUse=1G` ≈ 11h of
   local journal (FLEET_SERVICE_ATLAS.md §5 #10).
2. **Secret exposure, persisted 90d on a second host**: LogsQL against Bes
   VictoriaLogs (`http://100.110.156.91:9428/select/logsql/query`) —
   `_time:1d AND supergateway AND (Bearer OR "ghp_" OR "sk-" OR api_key OR
   apikey OR token OR secret OR password)` → **261** matching lines in the
   last 24h, **667** in the last 7d (masked count-only query; no raw values
   pulled — MASK BEFORE PRINT). This is a real hit rate, not a false alarm.

## Fix (this directory)

`--logLevel none` was considered and rejected: it also silences
`logger.error` (`dist/lib/getLogger.js:5-8`), losing `Child exited: code=…`
and transport-error diagnostics — worse for incident response.

Instead, split the unit's stdout/stderr at the systemd level:
- `no-body-logs-journald.conf` — drop-in redirecting both streams to a local,
  0600, `lumen:lumen`-owned file (`/home/lumen/logs/supergateway/desktop-commander-mcp.log`)
  instead of the journal. **journald and vlogs-forwarder never see it.**
- `supergateway-logrotate.conf` — caps that file at 50M × 4 rotations,
  compressed, so it can't repeat the journal's unbounded-volume problem.
- `apply.sh` — root-run installer, same quiet-window guard and health-check
  discipline as `ops/wq829-mcp-gateway/apply.sh` (this is the same unit;
  restarting it mid-request knocks over live sessions). Restarts via detached
  `systemd-run` per the Commander-path instinct, never foreground.

This worker's sandbox cannot write to `/etc/systemd/system/` or run
`systemctl restart` as root (same privilege-escalation denylist noted in
ISSUE-LOG #68) — `apply.sh` is a proposal for Frank/an authorized deploy path
to run, exactly like `wq829-fast-stop.conf` before it.

## wq#2546 follow-up — the local file itself was still unmasked

wq#2514's consumer scan (2026-09-25) found the live RealLumenHere PAT (fp
27ec5cf7418a) inside `desktop-commander-mcp.log` — the very file this
directory's fix created. Moving the body/chatter stream off Bes (above) never
masked its contents; `StandardOutput=append:<path>` is a raw fd redirect, so
nothing in the wq#1698 fix could have masked it.

Two independent mitigations now cover this file:
- `harness/mask_at_rest.py --store supergateway`, run nightly
  (`harness/systemd/lumen-mask-at-rest.{service,timer}`) and on every burn
  (`harness/mask_replay.py`'s `trigger_mask_at_rest()`), masks this file in
  place after the fact — already landed (wq#2546 build a/c/d).
- `wq2546-mask-body-log.conf` (this directory) stops the raw value from
  landing at all: it routes the body/chatter stream through
  `/home/lumen/bin/mask-secrets` before it hits the file, closing the window
  between a secret being printed and the next mask sweep.

`wq2546-mask-body-log.conf` and `apply-mask.sh` are proposals only, same
posture as `apply.sh` above and for the same reason: this worker's sandbox
cannot write `/etc/systemd/system/` or restart `desktop-commander-mcp` (the
very gateway this session's tools ride), and the Commander path changes only
via a careful, reviewed, detached `systemd-run` — never unattended. An
operator with that authority runs `apply-mask.sh` once `no-body-logs-journald.conf`
(wq#1698) is already live.

## What this does NOT cover

- **Purging the 667 already-shipped token-shaped Bes rows and rotating
  whatever they are** — needs someone with Bes VictoriaLogs delete access and
  credential-owner rotation rights to first identify which secrets they are
  without dumping them insecurely. Filed as a follow-up ticket (physical/
  investigative act — full-autonomy instinct's Frank carve-out), not blocked
  here.
- **Re-measuring journal retention after the fix lands** and the rest of
  atlas #10 (`RateLimitBurst`/`LogNamespace`) — depends on `apply.sh` actually
  running, which this worker cannot do.
