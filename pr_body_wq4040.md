## What

Applies the wq#4006 bundle (rescope `rea`/`rea-maksoor` off `~/.claude.json`
user scope to project scope under `~/re`, pin `rea-agents@6.5.0`, write
`PROVENANCE.json`, install the `rea-guard` self-update guard and
`HOSTILE_INPUT_POLICY.md`) against the live box, and adds a report
documenting and citing the evidence.

## Why

wq#4017 verified the bundle had shipped but never been applied: live
`~/.claude.json`/`~/re` still showed the pre-fix state (`install.sh --verify`
reported 5x MISS). Per the bundle's own RUNBOOK this is a privileged/
non-worker step — `~/.claude.json` and `~/re` sit outside a sandboxed BUILD
worker's granted dirs, and `~/.claude.json` gates every Claude Code session
on Lumen, so the apply needs session-visibility and filesystem access a
sandboxed worker does not have.

## How

- Confirmed no other fworker session was live (`ListAgents` — Commander's
  session registry, not a raw `ps` sweep) immediately before editing the
  shared `~/.claude.json`.
- Ran `WQ4006_REA_VERSION=6.5.0 bash ops/wq4006-rea-security-rescope-a1/install.sh`
  via `lumen-run` (detached systemd unit running as `lumen`, outside the
  per-tool sandbox — the same escape hatch wq#3846 used) — exit 0.
- Ran `install.sh --verify` via `lumen-run` — `VERIFY OK`.
- Confirmed `rea --version` reports the pinned `6.5.0` against the live
  install.
- `rea doctor --provider ghidra` exits 1 for two reasons unrelated to this
  ticket's scope: it flags the now-intentional absence from user-scope
  `~/.claude.json` as drift (exactly the fix's intended end state), and
  `GHIDRA_INSTALL_DIR` is unset in the job environment (an orthogonal
  provider-config gap this bundle never touched). Detailed in the report.
- `rea-maksoor`'s own guard/pin is explicitly out of scope per the RUNBOOK
  (separate ticket, remote host).

This repo's diff is docs-only (the real change is the live apply on the
box); `reports/wq4040_root-apply_wq4006-rea-security-rescope-a1.md` is the
evidence artifact the done-gate's citation points at.

## Testing

- `lumen-run ... install.sh --verify` -> `VERIFY OK` (quoted in the report)
- `rea --version` -> `6.5.0` (matches the pin)
- `ListAgents` confirmed no concurrent fworker session before the live edit
- `ops_verified(wq4006-rea-security-rescope-a1)` liveness probe
  (`harness/liveness.py:432-451`) now re-runs `install.sh --verify` against
  the live box and gets rc 0 (SATISFIED)

## Risk/rollback

Low risk: `install.sh` backs up `~/.claude.json` to
`~/.claude.json.pre-wq4006-<UTC timestamp>` before editing and is idempotent
(re-running it is safe). Already-running Claude Code sessions keep whatever
MCP connections they opened at startup (MCP servers are read once, not
hot-reloaded), so the edit only changes what *new* sessions outside `~/re`
see — no in-flight work was disrupted. Rollback: restore the newest
`~/.claude.json.pre-wq4006-*` backup over `~/.claude.json` and delete `~/re`
(per the bundle's own RUNBOOK "Revert" section).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
