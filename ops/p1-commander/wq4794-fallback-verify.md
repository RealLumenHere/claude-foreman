# wq#4794 — fallback Commander verify (2026-10-31)

Read-only verify per `commander.rule_fallback` (a fallback Commander must always
be live on a separate node), triggered by wq#4789's re-verify finding that
neith/khnum (lifecycle=retired 2026-10-27, wq#4788) can no longer satisfy it.

## Evidence

- `lumen_state current commander.topology` (fact id, `lumen_state.py`): mcpw's
  live `deploy_state` (version `189079a5`, live since 2026-09-28) carries
  `MCP_BACKENDS` with a single pool backend, `lumen` — no second entry.
- `ops/p1-commander/CHECKLIST.md` (the execute-on-arrival checklist for the
  planned P1 primary/backup HA hardware, wq#2744): still unchecked at step 0
  ("Unbox, note serial/model ...", "Flash to microSD") — the hardware was
  never provisioned, and no `p1-primary`/`p1-backup` host facts exist in
  `lumen_state`.
- `ops/p1-commander/mcpw-cutover-plan.md` §0: confirms the cutover is a config
  change (`MCP_BACKENDS` gains a second entry) gated on both P1 boxes passing
  a day of clean health checks — none of that has started.

## Conclusion

No live fallback Commander (cloud or node) currently exists. Recorded as:
- `lumen_state` fact `commander.topology` (superseded, observed 2026-10-31)
- `lumen_state` fact `commander.fallback` = `NONE LIVE as of 2026-10-31` (new
  predicate, with a `verify_recipe`: `curl` mcpw's `/pool/health` once
  `MCPW_POOL_HEALTH_SECRET` is bound (wq#3085), and grep `MCP_BACKENDS` in the
  mcpw Worker's `wrangler.toml` — a second live backend there means this
  fact is stale)

Build ticket filed to provide an actual fallback: wq#4797 (lane `frank-hw`,
`frank_gated`, `--deps 2744`) — either finish the P1 HA rollout or stand up an
alternative cloud fallback, then wire the second `MCP_BACKENDS` entry and
re-verify `/pool/health`. Not touched here: `commander.rule_no_unattended`
means nothing in mcpw or the control plane was changed by this ticket.
