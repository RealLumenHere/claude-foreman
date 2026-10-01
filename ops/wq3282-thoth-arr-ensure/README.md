# wq#3282 thoth-arr-ensure

prowlarr, sonarr, radarr, bazarr and qbittorrent-nox are `BindsTo=wg-quick@wg0` on thoth so they
can never run outside the VPN. systemd stops a BindsTo unit when wg0 stops and does not start it
when wg0 comes back, and `Restart=on-failure` ignores a clean stop. The 2026-09-08 tailnet-route
fix restarted wg0 and the whole stack stayed down until 2026-10-01. Only bazarr alarmed (it
timed out stopping and went `failed`); the other three were merely inactive.

Class: a unit that is stopped as a side effect and has no restarter. Fixed here for thoth with a
5 min timer; the fleet-wide half is the enabled-but-inactive check in fleet-update-check
(lumen-bin, same ticket).

## What it does
`thoth-arr-ensure` (/usr/local/sbin) runs from `thoth-arr-ensure.timer` every 5 min (3 min after
boot). Only if `wg-quick@wg0` is `active` it starts any of the five units that is enabled but not
active (`reset-failed` first when failed). It never stops or restarts anything and does not touch
wg0 or its drop-ins (thoth tailnet routing lives in wg0.conf PostUp, see lumen_state thoth
tailnet_routing). Exit 1 when a start fails, so the oneshot shows in `systemctl --failed`.

- wg0 down/activating: no-op, the kill-switch is meant to hold the stack down.
- unit disabled: left alone.
- `touch /etc/thoth-arr-ensure.pause`: skip everything until removed (maintenance).

## Install / verify / canary (thoth, sudo)
    scp -r ops/wq3282-thoth-arr-ensure thoth@100.75.1.108:/tmp/
    ssh thoth@100.75.1.108 'sudo bash /tmp/wq3282-thoth-arr-ensure/install.sh'
    ssh thoth@100.75.1.108 'sudo bash /tmp/wq3282-thoth-arr-ensure/canary.sh'   # stops sonarr, waits for the timer

## Rollback
    systemctl disable --now thoth-arr-ensure.timer
    rm /etc/systemd/system/thoth-arr-ensure.{service,timer} /usr/local/sbin/thoth-arr-ensure
    systemctl daemon-reload

## Tests
    python3 -m pytest -q ops/wq3282-thoth-arr-ensure/tests
