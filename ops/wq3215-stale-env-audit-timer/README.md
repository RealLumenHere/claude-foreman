# wq#3215 stale-env-audit timer

Runs `stale-env-audit --aq --docker` hourly as root. It compares every running service's process
environment with its current EnvironmentFile, and every running compose container's with its
`env_file`, and keeps one aq row per drifting unit/container (source `stale-env`, key = unit),
resolving it once that unit is clean. Variable NAMES only, never values.

Why: the Langfuse key rotation (wq#2906) changed agent/.env but four long-running units kept the
old key for two days (wq#3211). Fix a flagged unit with `systemctl restart <unit>`, or a flagged
container with `docker compose up -d --force-recreate <name>`; the row resolves itself on the
next run.

This bundle owns the **unit and timer only**. The binary comes from
`ops/wq3215-stale-env-audit-wire` — install that first (wq#3245), because the `--docker` in the
`ExecStart` needs its build, not the older `d35e196` one. Both `install.sh` and `--verify` check
that with `vflag`, so a timer wired to a flag the installed binary lacks is a red verify rather
than a silent whole-fleet audit (wq#3228).

Not covered: non-systemd, non-container processes (tmux/nohup — see the wire bundle's README for
why a `/proc` cmdline scan was rejected). Values pinned inline (systemd `Environment=`, compose
`environment:`) are reported but deliberately not alerted: no restart can change them.

Install: `bash install.sh` (sudo), or from a worker
`/home/lumen/bin/lumen-run start --root -- bash <abs path>/install.sh`.
Verify (read-only): `bash install.sh --verify`.
