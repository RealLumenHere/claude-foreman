# wq#980 — lock + unconditional backup for live fleet-config writes

## Root cause (from wq#976's verify-pass of wq#960, PR #150)

Two lanes mutated `/home/lumen/instincts.yaml` — a live, non-git-tracked fleet-config
file — within 14 minutes of each other on 2026-08-26, with no coordination:

- wq960 made a manual live edit (deleting a stale duplicate power-cycle-gate line) at
  ~20:24:44, but skipped its own change-safety.md G3 step for this one file: no
  `.bak..._pre_wq960` sibling exists, only a post-edit backup.
- wq959's `apply.sh` (`ops/wq959-instincts-tiering/apply.sh`) then wrote the whole file
  at 20:38:52, clobbering wq960's edit. That script only backed up on first schema
  migration (`! grep -q '^schema: 2'`) — once the file was already schema 2, every
  re-run overwrote it with **no backup at all**, exactly the wq959/wq960 collision
  scenario.

Net live state came out correct only because wq959's restructure independently retired
the same rule via supersession metadata — not because either lane's own safety net
caught the race.

## Fix: `fleet-safe-write`

`fleet-safe-write.sh` is a shared wrapper for any script or manual sweep that mutates
`instincts.yaml`, `manifest.yaml`, or `instructions/playbooks/*.md`:

- **Locks** the target file (`flock`, 30s timeout, one lockfile per basename under
  `/home/lumen/.locks/`) so a concurrent lane queues instead of racing.
- **Backs up unconditionally**, every call, before the mutation — `<file>.bak.<ts>_pre_<label>`
  — verified non-empty (`stat -c%s > 0`), matching change-safety.md G3 verbatim. This
  covers both failure modes at once: the lock prevents the interleaving that caused the
  actual clobber, and the unconditional backup means even a legitimate idempotent
  re-run (no lock contention, just an overwrite with identical content) still leaves the
  prior state reconstructable.

`ops/wq959-instincts-tiering/apply.sh` is updated to route both its `instincts.yaml` and
playbook writes through `fleet-safe-write` instead of its own conditional/cmp-gated
backup logic — the next time it (or a similar apply.sh) runs, it can't repeat this
failure mode.

wq960 had no `apply.sh` (its edit was a manual one-off, documented after the fact in its
own README) — there is no script to retrofit there. The fix for that class of lane is
procedural: any future manual live edit to these files should shell out through
`fleet-safe-write <file> <label> --cmd sed -i ... <file>` (or `--copy` a pre-staged
replacement) instead of an ad-hoc `cp -a` + edit, so the same lock and unconditional
backup apply regardless of whether the caller is a script or an interactive sweep.

## Usage

```
fleet-safe-write <target-file> <label> --copy <source-file>
fleet-safe-write <target-file> <label> --cmd  <command> [args...]
```

## Not done — outside this worktree's write scope

This session is sandboxed to its own git worktree; it cannot write to `/home/lumen/bin`
or `/home/lumen/instructions/` to install the tool live or add a "use fleet-safe-write"
note to change-safety.md's G3 section. `apply.sh` here (`cp
fleet-safe-write.sh /home/lumen/bin/fleet-safe-write`) does that install — it needs to be
run by a session with broader write scope (or Frank/root), same as wq960's staged
systemd unit. Until then this ticket's deliverable is the reviewed tool + the
already-safe wq959 apply.sh; the live install is the one remaining step.
