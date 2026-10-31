## What

Moves the real `claude_context.sh` boot script into a tracked location
(`ops/wq4790-claude-context-sh-into-bin/`, deployed by its `install.sh` over
`/home/lumen/bin/claude_context.sh`) and replaces the old `exec`-shim-only bin copy.

## Why

wq#4790: `/home/lumen/minerva/context/claude_context.sh` — the boot script every
session runs — had no git history, review, or rollback beyond ad-hoc `.bak` files,
because `minerva/.gitignore:18` ignores `context/` wholesale (it's meant for
generated/prose stores, not source). `/home/lumen/bin/claude_context.sh` was only an
`exec` shim into that untracked copy. The same session that found this (wq#4787)
also hit a defect version control would have caught: a stale `FLEET_TOOLS_BOOT.md`
injection sitting broken since 2026-10-27 because the script ran
`fleet-tools-render` without `--boot`.

## How

- Added `ops/wq4790-claude-context-sh-into-bin/claude_context.sh`: the real script,
  tracked and reviewable here (verbatim copy of the current minerva version —
  content/behavior unchanged, only its canonical home moves).
- Added `ops/wq4790-claude-context-sh-into-bin/install.sh`: idempotent deploy script
  (`unchanged:` on a no-op re-run, `--dry-run` supported) that installs the tracked
  copy over `/home/lumen/bin/claude_context.sh` — replacing the shim — via
  cp-to-tmp-then-`mv -f`, not an in-place overwrite: `guard.sh`'s PRIMARY_TREES wall
  denies a direct cp/edit of a live script there for exactly this reason (wq#2856 —
  a running process can still have the old inode open) and prescribes this
  atomic-rename shape as the sanctioned remedy.
- `context/` keeps holding what the script reads/writes at runtime
  (`SESSION-BRIEFING.md`, `FLEET_TOOLS_BOOT.md`, `sections.json`,
  `LIVE_FLEET_STATE.md`, `context_selector.py`) — generated/runtime state, which is
  what that gitignored directory should hold going forward.

## Testing

- `bash -n` passes on both new files.
- Ran `--core` on the original (minerva) copy and the relocated copy, from a
  sandboxed `/tmp` destination (the fworker sandbox's PRIMARY_TREES wall blocks a
  live write into `/home/lumen/bin`, so the comparison target is a staged copy, not
  the real `/home/lumen/bin/claude_context.sh`): both exited 1 with the identical
  `REFUSED: --core requires a registered wq identity (wq#1127)` message. The only
  line that differs is the self-referential `$0` path in the "Then re-run:" hint,
  which is expected — each script names its own invocation path.
- `install.sh` was run twice against a sandboxed fake destination: first run prints
  `installed ... -> ...`, second run prints `unchanged: ...` — confirms the
  idempotent/atomic-rename path works.

## Risk/rollback

Low. No logic in `claude_context.sh` changed, only its tracked location; the live
`/home/lumen/bin/claude_context.sh` is untouched until `install.sh` is actually run
against it (left to an operator/privileged lane or bin-drift-rescue, since a BUILD
worker's sandbox cannot write into that primary tree directly — same wall,
wq#900/wq#2856 — and `claude_context.sh`/`install.sh` aren't on the worker's
per-command Bash allowlist to run standalone either). Rollback: `git revert` this PR;
re-running the old shim content over `/home/lumen/bin/claude_context.sh` restores the
prior behavior, since `/home/lumen/minerva/context/claude_context.sh` is left in
place (untouched, still outside any worker's write access) until a follow-up cleans
it up.

## Follow-up (not done here, out of sandbox reach)

- Run `install.sh` for real against `/home/lumen/bin/claude_context.sh`.
- Once deployed, delete the now-orphaned
  `/home/lumen/minerva/context/claude_context.sh` so there's one canonical script.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
