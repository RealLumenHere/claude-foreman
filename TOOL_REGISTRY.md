# TOOL_REGISTRY — fleet-level tool adoption decisions

Tracks RE/dev tools evaluated for fleet-wide adoption (MCP registration through
Commander, cross-host install) that are significant enough to need a documented go/no-go,
distinct from the routine `/home/lumen/bin` inventory in minerva's FLEET_TOOLS.md.

## Maksoor RE workstation — verified tool inventory (2026-10-14, wq#3803)

Host: `maksoor` (user `maksoor`, tailnet 100.81.204.74; the `maksoor` ssh alias on Lumen now
resolves to this — it did not before wq#3803). Probed live over ssh on 2026-10-14. "PATH" below means the
non-interactive ssh PATH (what Commander/fworker/scripts see).

| Tool | Version | Location | On non-interactive PATH |
|---|---|---|---|
| Ghidra (+ `analyzeHeadless`) | 12.1.3 | /opt/ghidra (`ghidraRun` in /usr/local/bin) | analyzeHeadless: no (use /opt/ghidra/support/analyzeHeadless) |
| REA (`rea-agents`) | 6.4.0 | ~/.npm-global/bin/rea | NO — interactive .bashrc only; MCP entry uses the absolute path |
| binwalk | installed | /usr/bin/binwalk | yes |
| unblob | 26.6.4 | /usr/local/bin/unblob | yes |
| frida | 17.18.0 | /usr/local/bin/frida (+ /opt/re-venv) | yes |
| openocd | 0.12.0 | /usr/bin/openocd | yes |
| flashrom | 1.6.0 | /usr/sbin/flashrom | yes |
| radare2 | installed | /usr/bin/radare2 | yes |
| rkdeveloptool | installed | /usr/local/sbin/rkdeveloptool | yes |
| mtkclient (`mtk`) / edl | installed | /opt/re-venv/bin/{mtk,edl}; /opt/mtkclient | NO — venv path only |
| heimdall | installed | /usr/bin/heimdall | yes |
| ollama | NOT INSTALLED | — | — |

Notes: `rea` and `mtk`/`edl` are intentionally not on the system PATH (home/venv installs). Maksoor
also carries the longer tool list recorded in lumen_state (`maksoor.node_provisioning`: cutter, jadx,
apktool, scrcpy, sigrok, etc.); only the tools above were re-verified on 2026-10-14.
There is no local LLM runtime on Maksoor (`ollama` absent) — inference for RE work goes through Lumen/LiteLLM.

## REA (Reverse Engineer Anything) — github.com/morluto/rea / npm `rea-agents`

**Status (2026-10-14): INSTALLED on Lumen and Maksoor, Ghidra provider only. The original
SECURITY HOLD (below) is superseded by the installs, but its preconditions are NOT shown as met — see
"Open hold preconditions".**

Live state, verified 2026-10-14 (wq#3803):

- Package: `rea-agents@6.4.0`, npm global install on both hosts (`~/.npm-global`). `rea --version` -> 6.4.0.
- Lumen: `/home/lumen/.npm-global/bin/rea`. Non-interactive shells (ssh/Commander/fworker) now get
  `~/.npm-global/bin` on PATH plus `GHIDRA_INSTALL_DIR=/opt/ghidra_12.1.3_PUBLIC` and
  `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64` from the `rea-env wq3803` block in `~/.bashrc`
  (above the interactive guard, next to the wq3511 block; backup `.bashrc.bak.*_pre_wq3803`).
- Maksoor: `/home/maksoor/.npm-global/bin/rea`, Ghidra at `/opt/ghidra` (12.1.3). Not on the
  non-interactive PATH; the MCP entry calls it by absolute path.
- Provider: Ghidra 12.1.3 only (compatible 12.1.x). Hopper and IDA are NOT configured (optional, not needed).
- Skill: `reverse-engineer-anything` v37 / 170 tools, aligned on both hosts. Installed with
  `rea setup --yes --skill-client shared` (writes only `~/.agents/skills/reverse-engineer-anything`;
  no client config touched). `rea doctor --provider ghidra` on Lumen: healthy, environment_healthy.
- MCP registrations (in Lumen `~/.claude.json`, so every Claude Code session on Lumen sees them):
  `rea` (local, `rea mcp`) and `rea-maksoor` (ssh-stdio to maksoor@100.81.204.74, absolute path to rea).
  No other client (Cursor, Codex, Gemini CLI, ...) is detected or configured.
- ssh alias: `ssh maksoor` now resolves on Lumen (Host block in `~/.ssh/config`, backup `config.bak.*_pre_wq3803`).
- Not done (optional, wq#3803 item 4): pwntools venv / `REA_PWNTOOLS_PYTHON` for `inspect-binary-layout`.

### Open hold preconditions (from the original hold, still needing a human call)

The installs landed (wq#3786 follow-ups) without the registry being updated, so none of these is
recorded as satisfied anywhere in this repo. Frank should either accept the current state or require them:

1. Pin to a specific commit/release hash. Installed version is 6.4.0, but no commit hash / integrity
   record of what was installed or how (`npm install` vs `npx rea-agents setup`) exists here.
2. Read the setup script's install/postinstall hooks in an isolated throwaway environment first.
   No record that this was done.
3. Scope the MCP registration to a single non-production lane and scope Maksoor ADB access. Current
   registration is Lumen-wide for Claude Code (and `rea-maksoor` reaches the RE host that has live
   ADB/device access) — i.e. NOT scoped to one lane.
4. Re-check for security advisories once the package has had more scrutiny. Not re-run on 2026-10-14.

### Original decision (2026-10-13, superseded — kept for the record)

Ticket wq#3786 asked to install REA fleet-wide: `npx rea-agents setup` on Lumen, install on
Maksoor for device RE work, configure a Ghidra provider, and register it as an MCP server so
every agent gets it through Commander. The first pass recorded a SECURITY HOLD instead of installing:

- The repo is real and MIT-licensed (covered by multiple independent outlets — dev.to, Medium,
  gbhackers, kitploit — as an MCP server/CLI published on npm as `rea-agents`).
- The repo was ~2 weeks old and its GitHub star count went 120K -> 140K -> 171K over roughly a
  week (press timestamps from `fleet-search`, 2026-10-13) — the signature of bot-driven virality, the
  pattern used to pressure agents into auto-installing a new MCP server on social proof alone.
- No independent security audit or advisory was found either way for a 2-week-old package.
- Blast radius if wrong/intercepted: Ghidra execution + live ADB/APK access to a real device
  (Maksoor) + an MCP registration that hands every Commander-routed agent that access, on two hosts,
  for a package pinned to `latest` rather than a reviewed commit/version.
- Fleet precedent treats trending-tool adoption this way: `kb/tooling-findings.md` (2026-09-01)
  gates adoption of Langfuse/Gatus/etc. rather than installing on sight.

See `kb/ISSUE-LOG.md` row 1277 for the logged finding.
