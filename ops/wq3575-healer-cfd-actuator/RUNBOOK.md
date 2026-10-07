# wq#3575 — cfd-actuator stage 2: self-healer files requests

Stage 2 of `ops/wq1652-cfd-actuator/README.md`. The healer's cloudflared row gets
`restart_cmd: /home/lumen/bin/cfd-heal-request`, so a down tunnel files a
`cfd-actuator request --kind restart` and the healer no longer restarts the unit itself.

- Why a wrapper: `lumen_self_healer.restart_via_cmd` runs `subprocess.run([cmd])`
  (live `/home/lumen/agent/lumen_self_healer.py:224`, 2026-10-07): one argv element, no
  shell. The README's original one-line `restart_cmd` with arguments would have been
  exec'd as a single path that does not exist, and the cloudflared restart would have failed.
- Deploy (as lumen, no root): `bash ops/wq3575-healer-cfd-actuator/install.sh`. It installs
  the wrapper, patches the row (backup `healer_watch_table.yaml.pre-wq3575`) and prints
  the parsed row. The healer picks up the change on mtime.
- Live check: `install.sh --verify`.
- Behaviour: the healer sleeps 5 s after `restart_cmd`, then reads the unit as still
  down until the next drain tick (≤60 s). Ladder attempts 2 and 3 file more requests,
  and the actuator coalesces them into one restart. The 3-strike urgent page still fires
  if the actuator never brings the unit back.
- Tests: `python3 -m pytest -q ops/wq3575-healer-cfd-actuator/tests` (fixture table,
  never the live one).
- Revert: `cp -p healer_watch_table.yaml.pre-wq3575 healer_watch_table.yaml`, or delete
  the `restart_cmd` key from the cloudflared row.
