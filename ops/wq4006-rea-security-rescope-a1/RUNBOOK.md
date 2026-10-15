# wq#4006 — REA: user-scope MCP, unpinned (6.5.0), no provenance, unsandboxed capture

## Finding (emerging-re-tools-2026-10.md, session emerging-research-19b71e)

REA (`rea-agents`, github.com/morluto/rea) is registered as `rea` and
`rea-maksoor` in `/home/lumen/.claude.json` at **user scope** — every Claude
Code session run as `lumen` on Lumen, including every fworker, gets ~170 RE
tools whether the ticket needs them or not. `ghidra-lumen` by contrast is
project-scoped to `/data/lumen/archive/re`. The installed version drifted
from 6.4.0 (wq#3786/wq#3803, recorded in `TOOL_REGISTRY.md`) to 6.5.0 (npm
global install dir mtime 2026-10-14 22:26) with no PROVENANCE/SHA256 record,
unlike `/opt/ghidra-mcp/6.0.0/PROVENANCE`. Third-party writeups say REA's
runtime-capture tools (`capture_process_scenario`,
`instrument_with_frida`, the ADB tool family, …) run the target with the
invoking user's OS permissions — no sandbox. `TOOL_REGISTRY.md`'s own
"Open hold preconditions" section (written 2026-10-14, wq#3803) already
named three of these four gaps and never closed them.

## Fix shipped here (the class, not just this instance)

1. **Scope**: `rea.mcp.json.project` is the project-scope `.mcp.json`
   (mirrors `ghidra-lumen`'s pattern) — `rea` + `rea-maksoor` move out of
   `~/.claude.json`'s `mcpServers` entirely. Only a session opened in the
   project directory (default `~/re`) sees REA's tools; an unrelated
   fworker no longer gets them for free.
2. **Pin + provenance**: `generate_provenance.py` records the exact pinned
   version plus the npm registry's `dist.integrity` (sha512) and
   `dist.tarball` into `PROVENANCE.json`, in the project directory,
   matching the `/opt/ghidra-mcp/6.0.0/PROVENANCE` precedent the finding
   points at.
3. **Block self-update**: `rea-guard` is the only binary the project's
   `.mcp.json` ever invokes `rea` through. It refuses `update`,
   `self-update`, and `upgrade` subcommands outright (see the script) and
   execs the real pinned binary for everything else. `npx rea-agents@latest`
   bypasses PATH entirely (npx fetches from the registry), so it cannot be
   blocked at the wrapper layer — it is prohibited by policy instead
   (`HOSTILE_INPUT_POLICY.md` §3) and by the PROVENANCE pin (a version bump
   needs a new reviewed PROVENANCE entry, not a live `@latest` pull).
4. **Runtime capture isolation**: `HOSTILE_INPUT_POLICY.md` §2 states the
   rule the finding asked for — capture/instrument/ADB tool classes run
   only through `rea-maksoor` (the isolated Maksoor RE node), never against
   Lumen's own local `rea` registration, which stays Ghidra-provider-only
   (static analysis, never executes the target). This is a policy control,
   not a mechanically-enforced sandbox boundary — REA itself has no
   provider-level lock a Lumen-side file can flip; see "Not done" below.
5. **Hostile-input policy**: `HOSTILE_INPUT_POLICY.md` is the policy that
   "none exists" pointed at — adversarial-target handling, prompt-injection
   handling for target-derived strings, and the capture-isolation rule
   above, installed to `common/tools/` under the project root.

## What's NOT done (privileged/non-worker step)

Per `ops/VERIFY_CONTRACT.md`: a fix that edits a file outside a lane repo
ships as this bundle with an `install.sh`; merged is not live. Two separate
reasons apply here, both stronger than usual:

- `~/.claude.json` and `~/re` are outside this BUILD worker's granted dirs
  (same shape as `ops/wq3845-healer-drop-desktop-commander-mcp`'s
  `/home/lumen/agent/healer_watch_table.yaml` — the `cat`/`ls`/`Read` tools
  on either path return the sandbox's "outside allowed directories" refusal
  from this worktree, confirmed while researching this ticket).
- `~/.claude.json` gates **every** Claude Code session on Lumen. The ticket
  itself calls for "verify running fworkers unaffected" before editing —
  that check needs either live session enumeration (a full `ps aux`/`ps -ef`
  sweep, which is deliberately off this worker's allowlist per instinct 4:
  it would print other sessions' argv) or Commander's own session registry,
  neither of which a sandboxed BUILD worker can reach. This is exactly the
  kind of hard-to-reverse, shared-system action the operating protocol asks
  a worker to ship-but-not-execute rather than act on autonomously.

`install.sh` (no `--verify`) is the actual apply step for whoever/whatever
*does* have those dirs and that session visibility:

```
WQ4006_REA_VERSION=6.5.0 bash ops/wq4006-rea-security-rescope-a1/install.sh
```

It, in order: acquires `slock acquire wq4006-rea-rescope` (released via
`trap ... EXIT`), backs up `~/.claude.json` to
`~/.claude.json.pre-wq4006-<UTC timestamp>`, removes the `rea`/`rea-maksoor`
keys from its `mcpServers` block (`remove_user_scope.py`, idempotent),
writes `~/re/.mcp.json` + `~/re/bin/rea-guard` +
`~/re/common/tools/HOSTILE_INPUT_POLICY.md`, and runs
`generate_provenance.py` against the live npm registry to produce
`~/re/PROVENANCE.json`. Override `WQ4006_CLAUDE_JSON` /
`WQ4006_REA_PROJECT_DIR` to target anything other than the defaults
(`~/.claude.json` / `~/re`); `WQ4006_SKIP_SLOCK=1` is test-only.

Before running it live: confirm no fworker is mid-tool-call against `rea`/
`rea-maksoor` (Commander's session list, not a raw `ps` sweep), since the
edit removes the MCP registration a running session's Claude Code process
read at its own startup — an already-running session keeps whatever MCP
connections it already opened (MCP servers are read once at session start,
not hot-reloaded), so the edit is safe for in-flight work and only changes
what *new* sessions outside `~/re` see. Also run `rea --version` and
`rea doctor --provider ghidra` against the pinned 6.5.0 install once moved,
same as the wq#3803 verification, and apply the equivalent `rea-guard` +
pin on Maksoor separately — that host is outside this bundle's reach too
and needs its own ticket to cover `rea-maksoor`'s remote side symmetrically.

`install.sh --verify` is read-only and safe to run anytime (and from a
sandboxed worker, once `WQ4006_CLAUDE_JSON`/`WQ4006_REA_PROJECT_DIR` point
at the real paths it still cannot — those paths remain outside allowed
dirs even for `--verify`'s reads). It is exercised against temp fixtures in
`ops/tests/test_wq4006_verify.py`.

## Related tickets (noted, not executed here)

- `TOOL_REGISTRY.md`'s "Open hold preconditions" (wq#3803) already flagged
  pin/provenance and scoping; this bundle is the fix for items 1 and 3 of
  that list. Item 2 (read the setup script's install/postinstall hooks in
  an isolated throwaway environment) and item 4 (re-check advisories) are
  still open — out of scope for a rescope-only ticket.
- wq#3998 / wq#4000 are LIMPET dissection tickets for two *different*
  `rea` projects (`morluto/rea`, the one installed here, and
  `mytechnotalent/rea`, unrelated Rust tool) — both tickets' own notes
  already flag the name collision as a lookalike-repo risk worth tracking
  when either dissection names "rea" in a deliverable.

## Revert

Restore the newest `~/.claude.json.pre-wq4006-*` backup over
`~/.claude.json` and delete `~/re` (or just its `.mcp.json`/`PROVENANCE.json`
if other project content has since been added under it).
