# wq#4701: retire lumen-watchdog (do not reschedule it)

**Decision: retire.** The ticket offered two options: (a) dead code superseded by
Gatus/selfheal, retire it; (b) meant to run, add a timer. The evidence says (a).

## Evidence (2026-10-27)

- **The timer exists. It was switched off on purpose.** The ticket said "no
  lumen-watchdog.timer exists". It does: `/etc/systemd/system/lumen-watchdog.timer`
  (60 s, preserved in `units/`). `systemctl show` reports it `LoadState=loaded`,
  `UnitFileState=disabled`. `list-timers --all` misses it because a disabled timer
  nothing references is not loaded.
  `/home/lumen/agent/lumen_watchdog.RETIRED.txt` records why: "RETIRED 2026-08-27
  wq#970 — device/service coverage backfilled into Gatus".
- **Last scheduled run: 2026-08-27 19:42:46 UTC.** `/home/lumen/scratch/watchdog.log`
  (the unit's StandardOutput) has 147,854 lines, one a minute from
  2026-05-17 06:19:56 to 2026-08-27 19:42:46. After that the only line is
  2026-10-27 05:19:08, the manual wq#4695 acceptance run. `journalctl -u
  lumen-watchdog.service` (retention back to 2026-10-06) shows only that run, and
  `journalctl -u lumen-watchdog.timer` shows no entries.
- **Gatus covers every target, so a timer would page twice.** The live
  `/home/lumen/agent/lumen_watchdog.py` (post-wq#4695) checks:

  | watchdog target | Gatus endpoint (`:8418/api/v1/endpoints/statuses`) |
  |---|---|
  | DEVICES thor, thoth, 3dprinter, phone, anubis, maat | `1-nodes_<same name>` |
  | DEVICES lumen | none. The watchdog runs on lumen, so it cannot see lumen go down either |
  | SERVICES n8n, litellm, redis | `2-lumen-services_<same name>` |
  | SERVICES ntfy, beszel | `2-lumen-services_ntfy-self`, `_beszel-hub` |
  | `check_ollama()` (local :11434) | `2-lumen-services_ollama-local` |

  Gatus had polled each of these within the last minute when checked. Its alerts go
  through the `:8784/gatus-alert` bridge (`ops/wq1184-gatus-health-checks/config.yaml:42-44`).
  On a state change the watchdog sends `send_ntfy(..., priority="high")`. Both would page
  for the same outage.
- **Nothing downstream reads its output.** A grep for `HOMELAB_STATUS` across
  /home/lumen/agent, bin, foreman and minerva finds only the script itself.
  `lumen_watchdog_notify.py` is a different module, the heartbeat helper imported by
  lumen_self_healer, lumen_agent_v4 and lumen_fleet_researcher. It stays.

## What this bundle does

| File | Purpose |
|---|---|
| `remove-units.sh` | **root.** disable + delete both units and the `99-cron-stamp.conf` drop-in, daemon-reload, move the script to `lumen_watchdog.py.retired-wq4701`, update `lumen_watchdog.RETIRED.txt`, drop the stale dead-man stamp, record both units in `fleet-units/known-removed.txt`, re-baseline unit-drift |
| `install.sh --verify` | read-only wq#1696 check: both units `LoadState=not-found`, script moved, markers present |
| `units/` | verbatim copies of the two unit files, for rollback |

Also in this PR:
- `docs/fleet/retired_services.yaml` gets a `name: lumen-watchdog` entry.
- `ops/wq4695-watchdog-retired-canary-a1/` drops its `lumen_watchdog.py` copy and the
  matching verify/test lines. Otherwise `ops-deploy-drift` would report it as "not live"
  forever once the script is moved. The canary half of that bundle is unchanged.

## Apply

```
ssh lumen@100.117.66.102 'sudo bash /home/lumen/foreman/ops/wq4701-lumen-watchdog-retire/remove-units.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4701-lumen-watchdog-retire/install.sh --verify'   # expect VERIFY OK
```

This needs root, so a build lane cannot apply it (ISSUE-LOG #377/#979). Until it runs,
nothing changes on the host. The timer stays disabled and nothing pages.

## Liveness

The ticket's liveness line (`list-timers | grep lumen-watchdog.timer || crontab | grep
lumen_watchdog`) was written for option (b). Under the chosen option it is supposed to
stay false. The probe for (a) is `install.sh --verify` exiting 0.

## Rollback

```
sudo cp units/lumen-watchdog.{service,timer} /etc/systemd/system/
sudo -u lumen mv /home/lumen/agent/lumen_watchdog.py.retired-wq4701 /home/lumen/agent/lumen_watchdog.py
sudo systemctl daemon-reload && sudo systemctl enable --now lumen-watchdog.timer
```

Do this only after removing the overlapping Gatus alerts, or every outage pages twice.
