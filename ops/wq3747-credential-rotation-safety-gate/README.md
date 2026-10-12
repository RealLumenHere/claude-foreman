# wq#3747 — credential rotation safety gate

## The gap

`/home/lumen/bin/rotate-fleet-pw.sh`, `rotate-provider-keys`, `rotate-github-cred`,
`rotate-gitea-cred`, `gitea-rotate-finish`, and `github-pat-rotate` each destroy the
OLD value of a live credential the instant they write the NEW one. Every one of them
already keeps a local `*.bak.<ts>` copy for its own rollback, but nothing checks that
the old value also survives somewhere durable (Vault) before that destruction happens
— a bad rotation plus a lost/shredded local backup loses the credential for good.

## What ships in this PR

- `rotation_vault_gate.py` — the fine-grained gate: `require_backup(name, old_value)`
  checks a Vaultwarden secure note (`bw` CLI, reached only through whatever
  `BW_SESSION` the calling shell already unlocked — this module never reads or
  prompts for a master password), `log_rotation(...)` records every attempt as
  sha256[:12] fingerprints of old/new, never plaintext. Unit-tested with dummy
  credential values only (`test_rotation_vault_gate.py`); fails closed whenever
  Vault isn't reachable, which is also how the tests run fully offline.
- `vault_rotation_backup.py` — the attended-operator half: paste the OLD value,
  it goes into Vault under `rotation-backup/<name>/<date>`, and a local freshness
  marker is written for the harness-level check below.
- `harness/guard.sh` (+ `harness/tests/test_guard.py`) — a coarse, immediately-live
  backstop: a headless foreman BUILD worker's Bash tool call naming any of the six
  rotate-* tools is refused outright unless a same-day Vault-backup marker exists
  for that tool. This is enforcement a worker cannot route around, and it ships the
  moment this branch lands — no bin-repo change needed for it to take effect.

## What does NOT ship here, and why

The ticket also asks for `require_backup`/`log_rotation` to be called *inside* each
of the six `/home/lumen/bin` scripts, so the fine-grained check (does the backup's
VALUE match the credential about to be destroyed, not just "did an operator attest
recently") runs at the moment of rotation itself.

That needs edits to files in the **bin** repo, which is a separate repo from this one
(`/home/lumen/foreman`, where this worktree lives). `harness/guard.sh`'s own
WRITE_PRIMARY_TREES wall (wq#1719/wq#900) — correctly — refuses to let a worker write
inside `/home/lumen/bin` directly; the normal path is a worktree of the bin repo +
`lumen-pr`. wq#3747 was dispatched with a worktree of the **foreman** repo instead
(`/home/lumen/worktrees/wq3747-a1`, gitdir under `/home/lumen/foreman/.git/worktrees/`),
which has no path to the bin repo's own git history/PR review. Routing around that
with a raw `cp`/`install` of a modified `rotate-fleet-pw.sh` (sudo-password rotation
across the whole fleet) straight into the live `/home/lumen/bin` checkout — skipping
that repo's own review — was rejected as out of proportion to what this ticket's
access actually supports; see the RULES' "Land via lumen-pr... never direct-to-main"
and the general "check with the user before" guidance for hard-to-reverse, shared-system
actions.

**Follow-up** (recorded in `/home/lumen/kb/ISSUE-LOG.md`): dispatch a ticket with a
`bin`-repo worktree (`git -C /home/lumen/bin worktree add ...`, matching the existing
`/home/lumen/scratch/worktrees/bin-feat-*` pool) that imports `rotation_vault_gate`
(copy or path-reference this module) into each of the six scripts at the point each
one currently backs up its *.bak file, calling `require_backup_or_refuse` before the
overwrite and `log_rotation` after.
