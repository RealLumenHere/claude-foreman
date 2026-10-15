# REA: user-scope MCP, unpinned version, no provenance, unsandboxed capture

Status: FIX SHIPPED (bundle, not yet live-applied). wq#4006. Tree: claude-foreman,
worktree wq4006-a1. Builds on `TOOL_REGISTRY.md`'s REA section (wq#3786/wq#3803) and
`kb/ISSUE-LOG.md` row 1277 (original SECURITY HOLD) and row 1346 (this ticket's
finding+fix). Every claim below is a live-tree citation or carries the explicit reason
it could not be live-probed from this worktree.

## 0. What is actually true today (verified, not restated)

| finding claim | live state | verdict |
|---|---|---|
| `rea`/`rea-maksoor` registered at user scope in `~/.claude.json` | `TOOL_REGISTRY.md:52-53` (as of wq#3803, 2026-10-14): "MCP registrations (in Lumen `~/.claude.json`... every Claude Code session on Lumen sees them)". Could not re-read the live file directly this ticket — `cat`/`grep`/`Read` on `/home/lumen/.claude.json` from this worktree returns the sandbox's outside-allowed-directories refusal (same class of refusal `ops/wq3845-healer-drop-desktop-commander-mcp/RUNBOOK.md:3-4` hit against a different live file) | TRUE per the last recorded probe (wq#3803); not independently re-verified live this ticket |
| version drifted 6.4.0 -> 6.5.0 | `TOOL_REGISTRY.md:41` records 6.4.0 as of 2026-10-14; the ticket's own finding (session emerging-research-19b71e) reports 6.5.0 with an npm install dir mtime of 10-14 22:26. Not independently re-verified live this ticket (same reach limit — `~/.npm-global` is outside this worktree's granted dirs) | reported, not re-probed |
| no PROVENANCE/SHA256 unlike `/opt/ghidra-mcp/6.0.0` | `/opt/ghidra-mcp/6.0.0/PROVENANCE` itself is also outside this worktree's granted dirs (`cat` on it returned the same path-sandbox refusal) — could not diff its format directly; `TOOL_REGISTRY.md`'s REA section has no PROVENANCE reference at all, which is consistent with the finding | consistent, not directly diffed |
| ghidra-lumen is project-scoped to `/data/lumen/archive/re` | not independently verified — that path is outside this worktree's granted dirs and no committed `.mcp.json` for it exists in this repo to cite; taken as given from the ticket | unverified, taken as given |
| runtime capture is unsandboxed | REA's own MCP server instructions (loaded this session) list `capture_process_scenario`, `capture_native_ui_scenario`, `capture_electron_scenario`, `instrument_with_frida`, and the `adb_*` family under `rea`/`rea-maksoor`'s ~170-tool catalog; no sandbox/container flag is advertised by the tool list or by `TOOL_REGISTRY.md` | consistent with third-party report cited in the finding |

The common thread: **every live artifact this ticket is about (`~/.claude.json`,
`~/.npm-global`, `/opt/ghidra-mcp`, `/data/lumen/archive/re`) is outside a sandboxed
BUILD worker's granted directories.** That is not incidental — it is the same
boundary `ops/VERIFY_CONTRACT.md` and `ops/wq3845-healer-drop-desktop-commander-mcp`
already encode: a worktree can ship a bundle that edits fleet-shared state, but cannot
run it against the real files itself. This ticket's deliverable is that bundle.

## 1. Class, not instance

Class of defect: **a third-party MCP tool's blast radius (what it can reach, which
sessions get it, what version runs) is set once at install time and then drifts
silently**, because nothing re-checks it. `TOOL_REGISTRY.md`'s "Open hold
preconditions" section already named three of these four gaps on 2026-10-14
(wq#3803) and nothing closed them before the version moved 6.4.0 -> 6.5.0 on its own.
The fix has to be a standing control (scope + pin + guard + policy), not a one-time
edit, or the next npm update silently re-opens the same gap.

## 2. Fix shipped (`ops/wq4006-rea-security-rescope-a1/`)

| gap | artifact | mechanism |
|---|---|---|
| user-scope registration | `rea.mcp.json.project` | project-scope `.mcp.json` template for `~/re`, written by `install.sh`; `remove_user_scope.py:17-24` deletes the `rea`/`rea-maksoor` keys from `~/.claude.json`'s top-level `mcpServers` |
| unpinned version, no provenance | `generate_provenance.py` | pins an exact `<package>@<version>` and records npm's `dist.integrity` (sha512) + `dist.tarball` into `PROVENANCE.json`, mirroring the `/opt/ghidra-mcp/6.0.0/PROVENANCE` precedent the finding points at |
| self-update paths | `rea-guard:11-18` | the only binary the project's `.mcp.json` invokes `rea` through; refuses `update`/`self-update`/`upgrade` subcommands, execs the real pinned binary otherwise. `npx rea-agents@latest` bypasses PATH entirely (npx fetches from the registry on every invocation), so it cannot be blocked at the wrapper layer — it is a policy prohibition instead (`HOSTILE_INPUT_POLICY.md` §3), backed by the fact that a version bump now requires a new reviewed `PROVENANCE.json`, not a live pull |
| runtime capture unsandboxed | `HOSTILE_INPUT_POLICY.md` §2 | capture/instrument/ADB tool classes route through `rea-maksoor` (the isolated Maksoor node) only; the local `rea` registration stays Ghidra-provider-only (static, non-executing analysis). This is a **policy** control — REA has no provider-level lock a Lumen-side file can flip, so nothing here mechanically prevents a future config from pointing the local provider at something that executes targets. That gap stays open; see §4. |
| no hostile-input policy for RE tooling | `HOSTILE_INPUT_POLICY.md` (new; none existed at `re/common/tools` or anywhere else in the repo) | adversarial-target handling, prompt-injection handling for target-derived strings/decompiled code, and the capture-isolation rule above |

`install.sh` ties these together: `slock acquire wq4006-rea-rescope` ->
backup `~/.claude.json` -> `remove_user_scope.py` -> write `~/re/{.mcp.json,
bin/rea-guard, common/tools/HOSTILE_INPUT_POLICY.md}` -> `generate_provenance.py`.
Its `--verify` handler (read-only, `ops/_verify.sh` contract) checks all of the
above are in the fixed state, including that `rea`/`rea-maksoor` are **absent**
from user scope — the inverse of the usual "present and matching" check, because
presence is the bug. `ops/tests/test_wq4006_verify.py` exercises it against ten
cases (fixed state, drifted/still-registered state, each artifact missing
individually, malformed JSON) entirely against temp fixtures — 10/10 pass,
confirmed this ticket (`python3 ops/tests/test_wq4006_verify.py`).

## 3. Why the live cutover is not in this PR

Two independent reasons, both load-bearing:

1. **Reach.** `~/.claude.json` and `~/re` are outside this worktree's granted
   directories (`/home/lumen/agents/work-queue`, `mailbot`, `pokemon`, `bin`,
   `foreman`, `kb`, `sentinel`, `/tmp/fworker-wq4006`, plus the worktree itself).
   Every `cat`/`ls`/`Read` attempt against either path this ticket returned the
   sandbox's path refusal.
2. **Blast radius.** `~/.claude.json` gates every Claude Code session on Lumen.
   The ticket itself asks to "verify running fworkers unaffected" before editing
   — doing that honestly needs either a full session enumeration (a `ps aux`/
   `ps -ef` sweep, deliberately off this worker's allowlist because it would
   print other sessions' argv) or Commander's own session registry, neither
   reachable from here. `ops/VERIFY_CONTRACT.md`'s rule — ship the bundle,
   let a privileged/non-worker process apply it — exists for exactly this
   shape of change, and `ops/wq3845-healer-drop-desktop-commander-mcp` already
   set the precedent for a live file with the same outside-dirs refusal.

`ops/wq4006-rea-security-rescope-a1/RUNBOOK.md` carries the apply command and the
pre-apply checks for whoever/whatever runs it with the right dirs and session
visibility.

## 4. What's still open after this fix lands and is applied

- **Hold precondition #2** (read REA's setup script's install/postinstall hooks
  in an isolated throwaway environment before trusting it) — not done, not in
  scope for a rescope-only ticket.
- **Hold precondition #4** (re-check security advisories with more scrutiny) —
  not re-run. wq#3998/wq#4000 (LIMPET dissection tickets, in flight 2026-10-15)
  add *analysis* scrutiny of REA's own repo but are not a security-advisory
  re-check; both tickets' notes already flag the `morluto/rea` vs
  `mytechnotalent/rea` name collision as a lookalike-repo risk worth tracking.
- **Maksoor-side guard.** `rea-guard` and the version pin apply to Lumen's local
  `rea` invocation only. `rea-maksoor` still execs the real binary on Maksoor
  directly (`ssh maksoor@100.81.204.74 ... rea mcp`) with no equivalent guard on
  that host — a symmetric fix there is outside this worktree's reach (a
  different node entirely) and needs its own ticket.
- **Capture isolation is policy, not enforcement.** Nothing in this bundle stops
  a future REA config from registering a non-Ghidra, executing provider against
  Lumen's local `rea` entry — `HOSTILE_INPUT_POLICY.md` §2 states the rule;
  enforcing it mechanically would need either a provider-allowlist inside REA
  itself (upstream, not this fleet's to build) or a periodic audit comparable to
  `ops-deploy-drift` checking the project's `.mcp.json`/provider config against
  an expected shape. Not built here — flagged as the natural follow-up.

## 5. Revert

Restore the newest `~/.claude.json.pre-wq4006-<UTC timestamp>` backup over
`~/.claude.json`; delete `~/re` (or just `.mcp.json`/`PROVENANCE.json`/`bin/rea-guard`/
`common/tools/HOSTILE_INPUT_POLICY.md` under it, if other project content has since
been added).
