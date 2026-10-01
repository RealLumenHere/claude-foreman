# wq#3299 — bes-mcp-failover leaks stock supergateway handlers -> global OOM

## Problem (ticket, measured)

Bes runs stock supergateway (3.4.3, no reap) on `bes-mcp-failover.service`
(:3100/mcp). Per-request DC handlers never reap: 157 idle handlers, 13.1GB
cgroup, no `MemoryMax` on the unit. Global OOM 2026-09-30 21:20-21:22 PDT
killed HA python, the systemd user manager, and vmagent-nodeexp; Commander
failover was down ~5h. Interim hand-reap (2026-10-01 02:5x PDT: 70 idle
handlers >30min reaped, 12.5G->8.8G) regrows ~2GB/h, so OOM risk returns
within hours without a standing fix.

Lumen already carries the fix (wq#829/PR#151, deployed 2026-07-06):
`ops/wq829-mcp-gateway/stdioToStatelessStreamableHttp.js` adds
`reapRequestSession` — a per-request SIGTERM/SIGKILL sweep scoped to the
request's own process group (spares anything that double-forked or was
handed to a supervisor, e.g. `systemd-run`). That patch was never shipped to
Bes.

## What's here

- `stdioToStatelessStreamableHttp.js` — copy of Lumen's patched dist
  (`ops/wq829-mcp-gateway/stdioToStatelessStreamableHttp.js`, same file,
  reap logic at lines 73-… `reapRequestSession`).
- `99-wq3299-memlimit.conf` — drop-in for
  `/etc/systemd/system/bes-mcp-failover.service.d/`: `MemoryHigh=6G`,
  `MemoryMax=8G`, `TasksMax=512`. Bes's threshold is lower than Lumen's
  pressure-guard (11G) because Bes OOM'd at 13.1G with no other service
  headroom on the box.
- `mcp-pressure-guard-bes.sh` + `.service` + `.timer` — port of
  `/home/lumen/bin/mcp-pressure-guard.sh` to `bes-mcp-failover`, threshold
  6G, 600s cooldown. Verifies the restart actually advanced `MainPID`, not
  just that `/health` answers (a stuck process can keep serving cached
  `/health` while every real MCP call hangs).
- `apply.sh` — runs the whole rollout: discovers Bes's supergateway dist path
  at runtime (`npm root -g` over ssh, never a hardcoded guess), version-checks
  it against Lumen's before copying, backs up the live dist + drop-ins,
  arms a 180s dead-man rollback (wq#3108 pattern) *before* touching anything
  live, applies dist + memlimit drop-in + guard timer, restarts once
  (detached via `systemd-run`, never foreground), verifies `MainPID`
  advanced and `/health` returns 200, then disarms the dead-man. A failed
  check at any point lets the dead-man fire and restore the pre-change state
  automatically.

## Why this can't run from this worktree

BUILD-worker sandbox has no ssh/sudo path to Bes — confirmed 2026-10-01:
`fleet-exec run bes "id"` -> `DENY [grant:grant_missing_env]: grant file did
not verify`. Same posture as wq#3300's `apply.sh`/`apply-mask.sh` and
wq#829's `apply.sh`: these are proposals for an operator/session with that
access, run FROM Lumen. This is the one legitimate "needs a human" dependency
per the full-autonomy doctrine (instinct 3) — everything else in the ticket
(diagnosis, the patch source, the memlimit values, the guard port, the
dead-man wiring) is decided and built here, not parked on a question.

## Run order

1. `bash apply.sh` from an authorized session (ssh+sudo to Bes).
2. Confirm `mcp-pressure-guard-bes.timer` is active:
   `systemctl is-active mcp-pressure-guard-bes.timer` on Bes.
3. Watch `journalctl -u bes-mcp-failover -f` on Bes for ~1h to confirm
   cgroup memory holds flat instead of the ~2GB/h regrowth seen in the
   interim hand-reap window.
