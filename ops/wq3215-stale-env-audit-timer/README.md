# wq#3215 stale-env-audit timer

Runs `stale-env-audit --aq` (lumen-bin) hourly as root. It compares every running service's process
environment with its current EnvironmentFile and keeps one aq row per drifting unit (source
`stale-env`, key = unit), resolving it once the unit is clean. Variable NAMES only, never values.

Why: the Langfuse key rotation (wq#2906) changed agent/.env but four long-running units kept the
old key for two days (wq#3211). Fix a flagged unit with `systemctl restart <unit>`; the row
resolves itself on the next run.

Not covered: docker env_file drift (containers only re-read env_file on recreate), non-systemd
processes, values set only via Environment=. Rotation tools calling the audit as a gate is still open on wq#3215.

Install: `bash install.sh` (sudo). Verify (read-only): `bash install.sh --verify`.
