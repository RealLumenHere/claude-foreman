# wq#3511 — fleet tools on PATH for non-interactive ssh

## Problem
Commander `start_process` runs `ssh lumen@lumen '<cmd>'` → a non-interactive
bash. `command -v unit-drift lumen-run mask-secrets` all resolve to nothing;
only `/home/lumen/bin/<tool>` works. FLEET_TOOLS and the handoff document the
tools by bare name, so every fresh session trips, and `mask-secrets` guarded
with `command -v` silently never runs (MASK BEFORE PRINT instinct broken).

Class: PATH only set for interactive/login shells.

## Why ~/.bashrc top-of-file is the machinery-level fix
Probed on Lumen 2026-10-07 (`bash 5.3.9(1)-release`, login shell `/bin/bash`),
scratch-HOME probe, real `~/.bashrc` untouched:

| probe | result |
|---|---|
| `SSH_CLIENT=… bash -c` sources `~/.bashrc`? | **yes** (Debian SSH_SOURCE_BASHRC patch) |
| plain `bash -c` (no SSH_CLIENT) sources it? | no |
| `~/.bashrc` interactive guard | line 6 `case $- in *i*)…` returns first |
| any existing PATH line adds `/home/lumen/bin`? | no (all at lines 118+, below guard; `/home/lumen/bin` comes from `~/.profile`, login-only) |
| `/etc/environment` mentions `/home/lumen/bin`? | no |

So the one file every non-interactive ssh session on this box already reads is
`~/.bashrc` — but only the lines above the interactive guard. The fix inserts
the marker-fenced block in `bashrc-block.sh` at the very top: idempotent at
runtime (`case ":$PATH:"` dedupe) and at install time (marker grep).

Alternatives rejected:
- `/etc/environment` (pam_env): root-owned, all-users blast radius, static.
- `sshd_config PermitUserEnvironment` + `~/.ssh/environment`: requires editing
  and reloading sshd — the Commander path itself (instinct 5 territory) — for
  something the user-level file already solves.

## Install / verify / rollback
```
lumen-run start --name wq3511-path -- bash /home/lumen/foreman/ops/wq3511-noninteractive-path/install.sh
bash /home/lumen/foreman/ops/wq3511-noninteractive-path/install.sh --verify   # read-only, wq#1696 contract
```
Install backs up to `~/.bashrc.bak-wq3511-<ts>`, builds the candidate file,
`bash -n`-checks it, then atomically `mv`s into place. Verify is two-sided:
marker present AND an `env -i` probe (sshd-shaped baseline PATH, SSH_CLIENT
set) resolves `unit-drift lumen-run mask-secrets` — the probe can fail, and
does on an unpatched box (wq#2630). Install also runs the real Commander-path
probe: `ssh -o BatchMode=yes lumen@100.117.66.102 'command -v …'`.

Rollback: restore the printed backup, or delete the lines between the
`>>> wq3511` / `<<< wq3511` markers in `~/.bashrc`.

## Scope notes
- Fixes bash non-interactive ssh (the Commander/fworker shape). Does not cover
  `scp`/sftp-only sessions or a future non-bash login shell.
- Other fleet nodes with the same class of bug need the same block; this
  bundle targets Lumen, where FLEET_TOOLS' bare names are documented to work.
