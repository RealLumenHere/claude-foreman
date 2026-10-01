# wq#3288 — unit-drift baseline refresh (aq 3060)

aq 3060 (2026-10-01T03:11:50Z) flagged 47 added/changed units on lumen vs the
`/home/lumen/fleet-units` git baseline: auto-browser-mcp, dc-handles-scrub,
langfuse-lag-monitor, langfuse-trace-rate-monitor, litellm-completion-probe,
lumen-data-lumen-growth, lumen-disk-pct-alarm, and 40 more (full list via
`unit-drift check --digest`).

## Review

Ran `python3 /home/lumen/bin/unit-drift check --digest`. By the time of review
the live drift had grown to 49 changes (two more units landed between the aq
alert and this review). All entries trace to this week's shipped tickets:

- `ADDED` units: langfuse-lag-monitor, langfuse-trace-rate-monitor,
  litellm-completion-probe, lumen-disk-pct-alarm, lumen-stale-env-audit,
  lumen-minerva-guard, lumen-presentation-stability, mcp-gateway-watchdog,
  dc-handles-scrub, lumen-episode-spool-replay, lumen-federate-reindex,
  lumen-data-lumen-growth, auto-browser-mcp, mcpw-keepalive,
  lumen-workers.slice — all monitor/maintenance units matching this week's
  ticket names.
- `ADDED` drop-ins: cloudflared.service.d/10-control-plane-priority.conf,
  cloudflared.service.d/wq2606-metrics.conf,
  desktop-commander-mcp.service.d/{10-control-plane-priority,
  20-wq3103-commander-guard}.conf — named after wq2606/wq3103, both shipped.
- `REMOVED` unit + enable-link: nic-park.service — retirement, matches
  recent fleet cleanup pattern.
- `MODIFIED`: lumen-mask-at-rest.service, session-handoff.service — in-place
  unit edits from this week's work.
- `REMOVED` enable-link: chrome-mcp.service — disabled, consistent with the
  auto-browser-mcp addition replacing it.

No unexplained or unexplainable entries found. Matches aq 3060's own
assessment ("All appear to be legit shipped units from this week's
tickets").

## Action

Ran `python3 /home/lumen/bin/unit-drift snapshot`, committing the new state
to the fleet-units baseline repo as commit `ed610313` ("snapshot
2026-10-01T04:09:22Z: 458 units, 262 drop-ins, 359 enable-links"). Re-ran
`unit-drift check --digest`, which now reports `CLEAN (458 units, 262
drop-ins, 359 enable-links)`.

Resolved aq 3060 via `python3 /home/lumen/bin/aq resolve 3060 "unit-drift
snapshot taken after review; 49 changes confirmed as legit shipped units
(wq3288)"`.
