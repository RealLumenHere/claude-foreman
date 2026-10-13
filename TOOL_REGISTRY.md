# TOOL_REGISTRY — fleet-level tool adoption decisions

New file. Tracks RE/dev tools evaluated for fleet-wide adoption (MCP registration through
Commander, cross-host install) that are significant enough to need a documented go/no-go,
distinct from the routine `/home/lumen/bin` inventory in minerva's FLEET_TOOLS.md.

## REA (Reverse Engineer Anything) — github.com/morluto/rea / npm `rea-agents`

**Status: SECURITY HOLD — not installed on Lumen or Maksoor, not registered as an MCP server.**

Ticket wq#3786 asked to install REA fleet-wide: `npx rea-agents setup` on Lumen, install on
Maksoor for device RE work, configure a Ghidra provider, and register it as an MCP server so
every agent gets it through Commander.

The repo itself is real and MIT-licensed (confirmed via `fleet-search web`/`github`: covered by
multiple independent outlets — dev.to, Medium, gbhackers, kitploit — consistently describing an
MCP server/CLI published on npm as `rea-agents` that bridges coding agents to Ghidra, APK/ADB,
.NET, Electron, and packet analysis). That part checks out.

What stopped the live install:
- The repo is ~2 weeks old and its GitHub star count went 120K -> 140K -> 171K over roughly a
  week (press timestamps from `fleet-search`, 2026-10-13). That velocity is the signature of
  astroturfed/bot-driven virality, not organic adoption — exactly the pattern used to pressure
  agents into auto-installing a new MCP server across infrastructure on social proof alone.
- No independent security audit or advisory was found either way (`fleet-search web` for
  socket.dev/Snyk/npm-advisory turned up nothing specific to `rea-agents`). Absence of a
  flag is not the same as a clean bill of health for a 2-week-old package.
- The blast radius if it's ever intercepted/wrong is large: Ghidra execution + live ADB/APK
  access to a real device (Maksoor) + an MCP registration that would hand every Commander-routed
  agent that access, on two physical hosts, for a package pinned to `latest` rather than a
  reviewed commit/version.
- `npx *` and `npm install *` are on this worker's Bash allowlist, so nothing in the sandbox
  would have stopped the live install — this is a judgment call, not a gate the harness made
  for me. Made under the standing full-autonomy instruction (decide and act, no Frank-gate for a
  non-physical decision); recorded here rather than parked on a `wq block`.
- Fleet precedent already treats trending-tool adoption this way: `kb/tooling-findings.md`
  (2026-09-01) is a findings writeup gating adoption of Langfuse/Gatus/etc. rather than installing
  on sight. This entry follows that pattern.

Before any live install is reconsidered:
1. Pin to a specific commit/release hash, not `npm install rea-agents@latest`.
2. Read the setup script's actual source (install/postinstall hooks) before running it, in an
   isolated throwaway environment first — not directly on Lumen or Maksoor.
3. Scope the MCP registration (if any) to a single non-production agent lane before Commander-wide
   rollout, and scope Maksoor's device access (ADB) rather than granting it broadly.
4. Re-check for security advisories once the package has more than a couple weeks of scrutiny.

See `kb/ISSUE-LOG.md` row 1277 for the logged finding.
