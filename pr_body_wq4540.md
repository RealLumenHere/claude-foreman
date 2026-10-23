`harness/instincts_render.py` gains `--audience boot` (tier 0+1 whole, tier 2 titles only,
no rule text). This is the renderer `claude_context.sh --core` actually calls (wq#978), so
it lands directly in this branch.

Everything else the ticket names (`claude_context.sh`, `ORCHESTRATOR-HANDOFF.md`,
`LIVE_FLEET_STATE.md`, `/home/lumen/bin/fleet-tools-render`'s primary tree) lives under
`/home/lumen/minerva` or a primary tree this BUILD dispatch cannot write (`wq#1467`,
`wq#1719`/`wq#900`). Staged as `ops/wq4540-boot-budget-claude-context/` following wq#4537's
precedent: `fleet-tools-render --boot`, a new `live_fleet_state_cap.py` enforcing the
4000-tok soft cap that today only warns, and `handoff_s3_rewrite.py` (anchor-based,
archives the July-era HANDOFF s3 body to `sessions/` and replaces it with 5 live-command
lines) — 14 tests against fixtures, `install.sh` for a session with `minerva`/`lumen-bin`
fs access to apply the rest.

See that bundle's README for exactly what's staged vs landed, what's not done (the two
`claude_context.sh` wiring edits, and re-measuring the boot payload against the 45 KB
target), and a `guard.sh` gap found while investigating (ISSUE-LOG #1527/#1528).

Refs: wq#4540

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
