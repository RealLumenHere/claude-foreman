# wq#1184 — Gatus + cron dead-man runbook

## Deploy

```
lumen-run --root /home/lumen/foreman/ops/wq1184-gatus-health-checks/install.sh
```

Idempotent. What it does (see `install.sh` for the exact commands):

1. Builds Gatus `v5.36.0` from source (`go build`, CGO enabled for the
   sqlite3 driver) into `/home/lumen/bin/gatus`. Upstream ships no binary
   releases, only a docker image — `go build` is the only route to a
   "single Go binary, no docker" install.
2. Creates `/home/lumen/kb/gatus/{config.yaml,data/,gatus.env}`.
   `gatus.env` is created 0600 with an empty `LITELLM_MASTER_KEY` if it
   doesn't already exist — **never overwritten** on re-run.
3. Installs `gatus-ntfy-relay` and `lumen-cron-stamp` to `/home/lumen/bin/`.
4. Installs all 4 new systemd units plus the updated
   `lumen-foreman-{dispatch,selfheal,health}.service` (stamp hook added),
   then `daemon-reload`.
5. Enables + starts the ntfy relay and the cron dead-man timer immediately
   — both are safe with zero manual steps (see README.md's "why" section).
6. Leaves `lumen-gatus.service` installed but **not enabled** — see below.

## Enabling Gatus itself

1. Set the real key:
   ```
   sudo -u lumen vim /home/lumen/kb/gatus/gatus.env
   # LITELLM_MASTER_KEY=<the LiteLLM proxy master/virtual key>
   ```
   Never put this key in `config.yaml` or commit it — `gatus.env` is
   `EnvironmentFile=-` in the unit, 0600, outside git.
2. Re-read the HARD BRIDGE RULES in `config.yaml`'s header if you're about
   to hand-edit it (HTTP-only, Tailscale/loopback only, the two forbidden
   router IPs, >=60s intervals, transitions-only alerting).
3. **Prerequisite for the `foreman-heartbeat` endpoint**: it targets
   `127.0.0.1:8791/api/snapshot`, served by `lumen-foreman-dashboard.service`
   — staged but disabled by default (Tier-3, `dispatcher/systemd/INSTALL.md`).
   If it's not already running:
   ```
   systemctl enable --now lumen-foreman-dashboard.service
   ```
   Until then, that one endpoint will legitimately read FAIL.
3a. **Prerequisite for the `despag-uhp` endpoint**: it targets
   `127.0.0.1:8790/v1/uhp`, served by `lumen-despag.service` — staged but not
   installed by default (wq#1887 P2-1, `harness/systemd/lumen-despag.env.example`).
   Install the env file and unit per that ticket's PR body, then:
   ```
   systemctl enable --now lumen-despag.service
   ```
   Until then, that one endpoint will legitimately read FAIL.
4. Enable:
   ```
   systemctl enable --now lumen-gatus.service
   ```

## Verify

```
systemctl status lumen-gatus.service lumen-gatus-ntfy-relay.service lumen-cron-deadman.timer --no-pager
curl -s http://127.0.0.1:8783/api/v1/endpoints/statuses | jq '.[].name, .[].results[-1].success'
journalctl -u lumen-gatus.service -n 50 --no-pager
```

Force one dead-man pass and check its output:
```
systemctl start lumen-cron-deadman.service
journalctl -u lumen-cron-deadman.service -n 20 --no-pager
```

After a `lumen-foreman-{dispatch,selfheal,health}.service` run, confirm a
stamp landed:
```
ls -la /home/lumen/logs/.stamp.lumen-foreman-*
```

## Alert path sanity check

```
curl -s -X POST http://127.0.0.1:8784/gatus-alert \
  -H 'Content-Type: application/json' \
  -d '{"endpoint":"kb-mcp","description":"manual relay test — wq#1184","status":"TRIGGERED"}'
```
Should produce one `high`-priority ntfy notification titled
`Gatus TRIGGERED: kb-mcp`. This does not touch Gatus's own state — it's a
direct relay test only.

## Rollback

```
systemctl disable --now lumen-gatus.service lumen-gatus-ntfy-relay.service lumen-cron-deadman.timer
rm -f /etc/systemd/system/lumen-gatus.service /etc/systemd/system/lumen-gatus-ntfy-relay.service \
      /etc/systemd/system/lumen-cron-deadman.service /etc/systemd/system/lumen-cron-deadman.timer
systemctl daemon-reload
```
The `ExecStartPost` stamp hooks on the 3 foreman units are harmless to leave
in place (they only ever `touch` a file); revert those 3 `.service` files
from git if you want them fully gone too.

## Known trade-offs / follow-ups

- Dead-man coverage is currently 3 of ~68 live `lumen-*` timers (see
  README.md). Widening it is a matter of adding the `ExecStartPost` line to
  more units, one PR at a time — no dead-man code changes needed.
- `kb-mcp`'s endpoint uses a liveness-only condition (`[CONNECTED] == true`
  against `/mcp`) because it has no documented `/health` route; confirmed
  live that `/` and `/health` both 404 and `/mcp` returns 406 (still proves
  the listener is up and routing). If kb-mcp grows a real health route,
  tighten this condition.
- Gatus version is pinned (`v5.36.0` in `install.sh`) rather than tracking
  `@latest`, so re-running `install.sh` months from now reproduces the same
  build; bump it deliberately.
