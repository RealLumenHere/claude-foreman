# wq#5243 — baseline packages missing on thoth / ptah / j5005-2

Source: LRA Phase 2 read-only audit (wq#4999, 2026-11-21).

| Node | Missing | ssh target (lumen_state 2026-11-22) |
|---|---|---|
| thoth | tmux | `thoth@100.75.1.108`, key `/home/lumen/.ssh/thoth_ed25519` |
| ptah | tmux | `lumen@192.168.8.125` (LAN: the tailnet path needs interactive Tailscale-SSH approval) |
| j5005-2 | tmux, git | `jj5005-2@100.115.88.13` (ssh_user is UNVERIFIABLE in lumen_state; override with `WQ5243_DEST_j5005_2=user@host`) |

## Run (operator with a Lumen shell)

```
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5243-baseline-pkgs/install-baseline-pkgs.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5243-baseline-pkgs/install-baseline-pkgs.sh --apply'
```

The first command is a dry run: it prints present/MISSING per package per node and writes
nothing. `--apply` installs only what is missing. Add node names at the end to limit the
run (`... --apply j5005-2`).

Safety:
- Root access must be passwordless. If it is not, the node fails fast with exit 5 and the
  command to run by hand; nothing waits on a password prompt.
- An `apt-get -s` simulation runs first. Any removal or upgrade means exit 3 and nothing is
  installed.
- `NEEDRESTART_MODE=l` (needrestart 3.0 and later honour the env var over its config) keeps
  needrestart list-only, so no serving unit is restarted. Nothing reboots, and Thor is not
  a target.
- After installing, it checks the dpkg status again and runs `tmux -V` / `git --version`.

## Why a build worker did not apply it

Build workers have no root write path to fleet nodes. `fleet-exec` has no root verb yet
(`sudo_cmds` in `harness/fleet-exec-policy.yaml` is marked v2). The worker env also carries
no fleet-exec grant, and guard.sh denies the root-elevation verb outright. The sibling LRA
ticket hit the same wall (ISSUE-LOG #1710, wq#5244).

## Tests

`python3 -m pytest -q ops/wq5243-baseline-pkgs/test_install_baseline_pkgs.py` runs offline
with stub binaries (`WQ5243_LOCAL=1`). It checks the install path and every refusal path:
removal, upgrade, no passwordless root, and an unknown or Thor target.
