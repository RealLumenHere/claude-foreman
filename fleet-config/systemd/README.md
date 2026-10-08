# systemd units — where they live, how they install

Only the wq backup pair is authored here. Everything else already has an
authoritative home in the foreman repo; duplicating unit files invites
drift, so this directory maps them instead.

| units | authoritative source | installed by |
|---|---|---|
| `wq-backup.service` / `wq-backup.timer` | **this directory** | `workers` role |
| `lumen-foreman-*` (dispatch, gate@, health, janitor, mortician, selfheal, burnrate, window-gate, nightly-gate — .service + .timer) | `foreman/dispatcher/systemd/` | `workers` role (copies every `*.service`/`*.timer` found there) |
| `claude-shim.service` | `foreman/claude-shim/systemd/` | manual — see its own README |
| `token-engine.service` | `foreman/token-engine/systemd/` | manual |
| `lumen-prompt-forge.service` | `foreman/prompt-forge/systemd/` | manual |
| `gatus-ntfy-relay.service`, `gatus-sshauth-probe.service` | live on lumen under `/etc/systemd/system/` (captured by `scripts/backup.sh`) | restore from backup (RECOVERY.md §5) |

Manual install of any unit:

```bash
sudo cp <src>.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now <unit>
```

~300 fleet-authored units exist on lumen in total
(`research/FLEET_SERVICE_ATLAS.md`). The backup script captures all of
`/etc/systemd/system` precisely so a rebuild does not depend on each one
having been IaC-ified first.
