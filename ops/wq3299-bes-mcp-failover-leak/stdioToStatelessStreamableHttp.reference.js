// wq#3299 reference copy — Lumen's patched supergateway 3.4.3 dist
// (gateways/stdioToStatelessStreamableHttp.js), narrowed per-request reap
// (upstream #108), deployed to Lumen only on 2026-07-06. Bes still runs the
// stock gateway with no reap at all, which is the root cause of the handler
// leak (157 idle handlers, 13.1GB cgroup, no MemoryMax -> global OOM
// 2026-09-30 21:20 PDT). Captured verbatim from
// /home/lumen/foreman/_runs/wq2435/snapshots/20260924T111531Z-...-stdioToStatelessStreamableHttp.js
// (Lumen host, post-patch) for apply.sh's version-matched copy to Bes — see
// apply.sh's `arm` step, which refuses to copy unless staged and live
// package.json versions match.
//
// Key patch, lines 11-71 and 142-158 of the live file:
//   - reapGraceMs(): env-tunable SIGKILL grace window after SIGTERM (default 2000ms)
//   - reapRequestSession(pgid, signal): walks /proc, SIGKILLs only processes
//     still in this request's own process group AND session (pgrp===pgid &&
//     sid===pgid && ppid!==1) — anything that detached (setsid, systemd-run,
//     reparented to init) is spared instead of leaking as an orphaned idle
//     handler.
//   - res.on('close'/'finish', reapChild): SIGTERM the whole request group
//     immediately when the HTTP response ends, then SIGKILL-sweep via
//     reapRequestSession() after the grace window.
//
// This file is NOT imported or built from — it is a pinned citation/reference
// for AC1 and the payload apply.sh ships to Bes. The authoritative patched
// source stays on Lumen at ~/.npm-global/lib/node_modules/supergateway/dist/.
