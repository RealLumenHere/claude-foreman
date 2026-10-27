# wq#4706 — gate-snapshot-prune timer

The scheduler wq#4654 shipped without.

## What was wrong

wq#4654 (PR #2473) added `prune_vault()` and an `action_gate.py prune-vault` entry point to
retire the gate-snapshot vault's aged non-credential pre-images, and **nothing called it**.
`git grep prune-vault` at the PR head matched only `harness/action_gate.py` and its tests —
no timer, no cron, no sweep. wq#4657 caught that, and wq#4706 is the fix. Merged as it stood,
the PR would not have retired a single file; the vault went 2415 files / 1.6G at wq#4654's
filing to 2440 / 1.7G a day later, still compounding.

Two separate things were therefore wrong, and the second is the one this bundle addresses:

1. The PR was unmerged when wq#4654 closed (`--unmerged` on the record). Landed in wq#4706 as
   merge commit `2f2c617a`.
2. The code had no caller. That is this bundle.

## Why this unit, and not a call inside secret-backup-sweep

`secret-backup-sweep` (wq#4599, `/home/lumen/bin/secret-backup-sweep`, daily 04:40) was the
other candidate owner, and the vault is already its territory — it is the thing that retires
secret-BEARING pre-images by proving the value dead. Folding `prune_vault()` into it would
have meant one unit instead of two. Rejected, for three reasons:

- **The scheduler belongs in the repo that holds the code it runs.** `prune_vault` lives in
  `claude-foreman/harness/action_gate.py`. `secret-backup-sweep` lives in `lumen-bin`. A
  caller across that seam means a change to `action_gate.py`'s CLI can silently break a unit
  in another repo, and neither repo's tests would see it. `ops-deploy-drift` and the wq#2943
  ops-deploy gate both key off `claude-foreman/ops/*`, so a bundle here is the one that gets
  watched.
- **Its exit codes are already carrying a signal.** `secret-backup-sweep.service` sets
  `SuccessExitStatus=1` because exit 1 means "an ORPHAN_LIVE credential was found" — a
  finding, not a failure. Adding a second job under that unit means a prune failure either
  disappears into the overloaded success set or pages as a credential orphan. Separate units,
  separate `OnFailure=ntfy-onfailure@%n`.
- **Different jobs, different proofs.** The sweep's job is a value-level dead-check; this
  one's is a blunt age cutoff for the `NO_SECRETS` class the sweep structurally never claims
  (`cmd_apply` only shreds `verdict=="ELIGIBLE"`). They are complements, not one job.

Ordering is preserved anyway: 05:10 runs after the sweep's 04:40 window (+10min
`RandomizedDelaySec`, 600s timeout), and `After=secret-backup-sweep.service` states the
intent. Sweep retires what it can prove dead; this pass ages out the residue.

## What it does not do

It does not touch a pre-image that still carries a secret pattern, at any age — those are
`secret-backup-sweep`'s to retire by proving the value dead, and racing it would destroy the
evidence that proof needs. `_carries_secret_pattern()` fails closed (unreadable, binary, no
`read_dir_sweep` import → treated as carrying a secret → kept).

Retention window: 30 days, `FOREMAN_GATE_SNAP_PRUNE_DAYS`. That is a rollback window, not a
dead-value proof.

## Install / verify

    sudo lumen-run /home/lumen/foreman/ops/wq4706-gate-snapshot-prune-timer/install.sh
    /home/lumen/foreman/ops/wq4706-gate-snapshot-prune-timer/install.sh --verify

`--verify` does the usual unit-file and enabled/active checks, and then two more, because a
`vfile` + `vactive` pair proves a unit is installed and says nothing about whether its
`ExecStart` still works — which is the exact class of lie wq#4654 shipped:

- `vcontains` on the live `action_gate.py` for `def prune_vault(... dry_run` — asks the callee
  what it implements (wq#3228: a gate keyed to a flag the installed build never had).
- a real `prune-vault --dry-run` against `/home/lumen/.secrets/gate-snapshots`, read-only,
  asserting the summary line names that vault. The vault is passed explicitly rather than
  left to `expanduser("~")`: under root the default resolves to `/root/.secrets`, which is
  absent, so `prune_vault` would return `[]` and the probe could not fail (wq#2630).

`prune-vault --dry-run` (wq#4706, `harness/action_gate.py`) selects the same set and unlinks
nothing — an installer that has to delete files to prove itself is not a pre-flight check.
