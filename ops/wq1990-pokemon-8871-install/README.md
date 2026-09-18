# wq#1990 — install wq#1661 :8871 units as root

Root-install for `deploy/lumen-pokemon-live.service` + `deploy/lumen-kiosk-idle.service`
(wq#1661, ec1bf29, PR#112 on `/home/lumen/pokemon`), which were merged to main but
never installed. Procedure and file contract: `/home/lumen/pokemon/docs/PORT-8871.md`.

## Run

```
/home/lumen/bin/lumen-run start --root --name wq1990-install -- \
    /home/lumen/worktrees/wq1990-a1/ops/wq1990-pokemon-8871-install/apply.sh
/home/lumen/bin/lumen-run wait <UNIT>
/home/lumen/bin/lumen-run log <UNIT>
```

Then the real restart (the only sanctioned path):

```
/home/lumen/bin/lumen-run start --name wq1990-restart -- \
    /home/lumen/bin/lpp-restart --ticket wq#1990
/home/lumen/bin/lumen-run wait <UNIT>
```

## What it does

1. Unmasks `lumen-pokemon-live.service` (masked since >= 2026-09-13, wq#1895; see
   `apply.sh` header for why this ticket unmasks rather than leaving it parked).
2. Copies both unit files from `deploy/` into `/etc/systemd/system/`, backing up
   whatever was there first (`/home/lumen/backups/wq1990-pokemon-8871/`).
3. `systemctl daemon-reload` if anything changed.
4. Deletes the superseded `/home/lumen/bin/lpp-cover-maybe-up` and
   `/home/lumen/bin/lpp-idle-guard` (backed up first) — the installed units no
   longer reference them; their job is fully absorbed by `tools/lpp-cover-serve`
   and `tools/lpp-cover-maybe-up` in the pokemon repo.

Idempotent: safe to re-run.

## Verify

```
python3 /home/lumen/pokemon/tools/test_1661_port_8871.py   # 8/8, no root needed
journalctl -u lumen-pokemon-live -u lumen-kiosk-idle --since "-5min" --no-pager
```

A real restart must show `lumen-pokemon-live` active with a fresh event and no
`lumen-kiosk-idle` start in the journal (the wq#1661 defect this ticket ends).
