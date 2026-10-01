# wq#3299 — bes-mcp-failover leaks stock supergateway handlers -> global OOM

## Root cause

Bes runs stock `supergateway` 3.4.3 for `bes-mcp-failover.service`
(supergateway + desktop-commander on :3100/mcp). Stock supergateway never
reaps per-request stateless-mode child processes/handlers. Lumen's own copy
was patched 2026-07-06 (upstream issue #108: narrowed per-request reap — see
`stdioToStatelessStreamableHttp.reference.js` in this dir, lines 11-71 and
134-158 of the live file) but that patch was never shipped to Bes.

Result 2026-09-30 21:20-21:22 PDT: 157 idle handlers, 13.1GB cgroup, no
`MemoryMax` on the unit -> global OOM killed HA python, the systemd user
manager, and vmagent-nodeexp; Commander failover was down ~5h. Interim
hand-reap on 2026-10-01 (70 idle handlers >30min, 12.5GB -> 8.8GB) only
buys hours — leak rate is ~2GB/h.

## Fix, three parts

1. **Ship the patched dist.** `apply.sh` rsyncs/copies Lumen's patched
   `supergateway` dist tree to Bes, but only after confirming the staged and
   live `package.json` `version` fields match (ticket: "same version, check
   first") — refuses the copy on a mismatch rather than silently upgrading
   Bes to an untested supergateway release mid-incident.
2. **Cap the cgroup.** `bes-mcp-failover.service.d/99-wq3299-memlimit.conf`
   adds `MemoryHigh=6G` (throttle/reclaim before it's fatal) and
   `MemoryMax=8G` + `TasksMax=512` (hard ceiling, so a regression degrades the
   one unit instead of OOM-killing the host again).
3. **Port the pressure guard.** `mcp-pressure-guard-bes.sh` +
   `.service`/`.timer` are Bes-specific copies of Lumen's
   `/home/lumen/bin/mcp-pressure-guard.sh`, re-pointed at `bes-mcp-failover`
   and `THRESH_MEM_G=6` (matching the new `MemoryHigh`). Restart verification
   checks that `MainPID` actually changed, not just that `/health` returns
   200 — a stuck/respawning child can serve a stale 200 on the same port.

## Deploy

`apply.sh` follows the wq#3108 dead-man pattern (see
`ops/wq3108-fleet-input-default-drop/apply.sh`): arm backs up the live dist +
unit drop-in, installs the patch, restarts, and arms a 180s `systemd-run`
rollback timer; disarm cancels the timer once the restart is verified
(`MainPID` advanced) and the fix holds live. Restart is safe — the gateway is
stateless per request — but a bad dist copy is not, hence the dead-man.

Run ON Bes as root (this is a privileged, host-level action outside the BUILD
worker sandbox — the sandbox's deny-list blocks any `sudo`/root verb even
embedded in a remote SSH command, so this script is handed off rather than
executed from the worktree):

```
sudo systemd-run --unit wq3299-apply-bes bash apply.sh arm <staged-dist-dir>
# verify from Lumen: curl -sf https://mcp-failover.lumennode.uk/health,
# confirm HA/Commander still answering, confirm bes-mcp-failover MainPID != pre-restart PID
sudo bash apply.sh disarm <staged-dist-dir>
```

`<staged-dist-dir>` = a copy of Lumen's
`~/.npm-global/lib/node_modules/supergateway/dist/` rsynced to Bes alongside
this ops dir.
