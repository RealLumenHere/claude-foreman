# wq#1656 — retire lumen-rerank-thread-watch timer (48h watch, 27 days old)

Found by wq#1627 (`research/FLEET_SERVICE_ATLAS.md` finding F-10, line 360/560):
wq#596 installed `lumen-rerank-thread-watch.timer` on 2026-08-14 as a "48h
auto-expiring" 5-min watch for a gateway restart or sustained memory PSI
during the pinned 16-thread rerank arm rollout. 27 days later it is still
installed, enabled, and firing every 5 min; the one-shot DONE marker makes
every run a no-op.

## Sweep

`systemctl show`/`cat` on 2026-09-11:

| unit | LoadState | ActiveState | UnitFileState |
|---|---|---|---|
| lumen-rerank-thread-watch.timer | loaded | active | enabled |
| lumen-rerank-thread-watch.service | loaded | inactive | static |

`systemctl cat lumen-rerank-thread-watch.service`:
`ExecStart=/home/lumen/bin/rerank-thread-watch.sh`.

`/home/lumen/bin/rerank-thread-watch.sh:57` — `[ -f "$DONE" ] && exit 0`,
where `DONE=/home/lumen/minerva/v4/reports/596_watch_ROLLED_BACK`. The script
header (lines 1-19) documents the design: either trip condition (gateway
restart off baseline, or 2 consecutive memory-PSI samples >=25%) runs the
rollback once and writes the DONE marker so a later unrelated restart can't
loop. `journalctl -u lumen-rerank-thread-watch.service --since -30min` on
2026-09-11 shows a bare `Starting... / Deactivated successfully. / Finished...`
cycle every 5 min (03:05, 03:10, 03:15, 03:20, 03:25, 03:30Z sampled) — no
rollback or ntfy activity, consistent with the DONE-marker short-circuit
firing on every run: the watch already tripped and rolled back once, long
before this audit, and has been a no-op ever since (matches the atlas F-10
finding).

## Apply (this ticket, wq#1656)

Documented the retirement in `docs/fleet/retired_services.yaml` (`name:
lumen-rerank-thread-watch` entry) per the file's own schema/convention
(mirrors the `agent5-split` entry from wq#1643).

Removing the live units (`systemctl stop`/`disable`/`mask` + deleting the
two files under `/etc/systemd/system/`) is outside this build lane's
permitted actions — the house deny-list blocks the systemctl verbs that
stop, disable, or mask a unit, and this lane has no systemctl-mutate grant
(same restriction hit by wq#1643 for `lumen-split-watch.timer`). Unlike
wq#1643, there is no guard script here to make retirement-aware — the
service already self-neutralizes via its own DONE marker — so there is no
code diff to file from this lane, only the live unit removal.

Filed for a lane with systemctl-mutate access: **wq#1762** — take
`lumen-rerank-thread-watch.timer` out of service and off its enable-state,
delete `/etc/systemd/system/lumen-rerank-thread-watch.timer` and
`.service`, `systemctl daemon-reload`, confirm both units read "could not
be found" on `systemctl status`, then `unit-drift snapshot` to fold the
removal into the `/home/lumen/fleet-units` baseline (otherwise `unit-drift
check` would flag the disappearance as unexpected drift on its next run).

## Verify

Once wq#1762 lands: `systemctl status lumen-rerank-thread-watch.timer` and
`.service` both report "could not be found"; `unit-drift check --digest`
shows no drift for these two units afterward.

## Rollback

The four data files under `/home/lumen/minerva/v4/reports/596_watch*` are
left in place (harmless once the timer is gone). If the watch needs to come
back, reinstall the two unit files (see `systemctl cat` output above) and
remove the `596_watch_ROLLED_BACK` DONE marker so the script re-arms.
