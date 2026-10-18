# wq#4307 verify of wq#4301 (retire stealth-browser-mcp :8900, PR 2205): CONFIRM

Verifier: design-s-a21137 (claude-sonnet-5.5), 2026-10-18.

The merged diff is docs only (TOOL_REGISTRY.md, 4+/2-), so the claim under test is live state. All held:
- `systemctl is-active stealth-browser-mcp.service` = inactive; is-enabled = not-found (unit no longer loadable).
- Unit parked at `/etc/systemd/system/stealth-browser-mcp.service.retired-wq4290` (root, 505 B), matching the doc text.
- Nothing listening on :8900 (`ss -ltnp`).
- `bash ops/wq4206-stealth-browser-mcp/install.sh --verify` = VERIFY OK, rc 0 (guarded copy healthy; retire script present for `--restore`).
- No live consumer of :8900: grep across agent/, minerva/, bin/, /etc/systemd/system, foreman ops/dispatcher/orchestrator found only
  the bundle itself, the parked unit, a memory log and an unrelated emba-dist file.
Not covered: per-user client configs outside those trees (e.g. ~/.claude*.json). Low risk: retire script reports the port closed.
