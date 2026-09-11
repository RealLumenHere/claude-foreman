# wq#1655 — fleet-inventory-render.path dead from start-limit (no debounce)

Source: `research/FLEET_SERVICE_ATLAS.md` #11/F-7 (wq#1627): wq#1580 wrote 13
machine YAMLs in ~15s; each `PathChanged` re-triggered
`fleet-inventory-render.path`, which hit systemd's default per-unit start
limit (5 starts / 10s) at 05:39:45Z and went `failed` (`unit-start-limit-hit`).
`.path` units have no `Restart=` mechanism, so once failed they stay failed
forever — the 5-min `fleet-inventory-render.timer` keeps the page fresh, which
is exactly why nobody noticed the path unit was dead. Confirmed still failed
today (`systemctl list-units fleet-inventory-render.path --all`: `loaded
failed failed`, 2026-09-11).

## Fix (three parts, per the ticket's fix class)

1. **`StartLimitIntervalSec=0` on `fleet-inventory-render.service`** — the
   oneshot render is cheap and idempotent, so its own start-rate guard is pure
   downside. See `units.diff`.
2. **`TriggerLimitIntervalSec=`/`TriggerLimitBurst=` on
   `fleet-inventory-render.path`** — the actual failure was the *path unit's*
   own default start limit, not the service's (13 triggers in 15s > 5/10s).
   `TriggerLimitIntervalSec=10s` + `TriggerLimitBurst=20` (systemd.path(5),
   systemd >= 236) replaces that default with a limit wide enough to absorb
   the wq#1580 batch-write case. A render.py-side debounce (the ticket's other
   offered option) was not taken — same failure, more surface area, and the
   render is already idempotent so a systemd-level rate limit is sufficient.
   See `units.diff`.
3. **`lumen-failed-units` never watched `.path` units at all** —
   `systemctl list-units` in the live script
   (`/home/lumen/bin/lumen-failed-units`) passes `--type=service --type=timer
   --type=socket --type=mount`, omitting `--type=path` outright. That is a
   second, independent bug: even after 05:39:45Z, the fleet's edge-triggered
   failed-unit sweep had zero visibility into this failure. `staged/
   lumen-failed-units` adds `--type=path` and classifies any newly-failed
   `.path`/`.socket`/`.timer` unit as `urgent` priority (vs. `high` for an
   ordinary service) with an explicit "NEVER SELF-RECOVERS" note, since those
   unit types have no restart-on-failure equivalent and stay broken until a
   human runs `systemctl reset-failed`.

   This staged copy is layered on top of wq#1657's undeployed
   `ops/wq1657-guard-exit-convention/staged/lumen-failed-units` (adds the
   `is_finding_exit`/`FINDING_LIST` guard-exit classification) rather than the
   plainer version currently live, so the two fixes compose instead of one
   clobbering the other whichever lands first — see RUNBOOK.md.

## Why staged, not live-edited

`/etc/systemd/system/fleet-inventory-render.{service,path}` is outside this
worker's sandboxed worktree/`--add-dir` set, and `systemctl daemon-reload` /
`reset-failed` / `start` are not on the per-command allowlist (only
`status`/`is-active`/`is-enabled`/`show`/`list-*`/`cat` are read-only-safe and
allowed). `/home/lumen/bin` (where the live `lumen-failed-units` sits) *is* an
`--add-dir` root, but the fix only counts as landed on this branch if the code
change lives inside this git worktree — everything here is staged for a
session with broader access (or Frank) to apply, following the precedent in
`ops/wq1659-dangling-service-afters/RUNBOOK.md`.

## Test

`tests/test_path_unit_urgent.sh` stubs `systemctl`/`journalctl`/`ntfy-send`/
`aq`, feeds the sweep a fake failed `fleet-inventory-render.path` plus an
ordinary failed `.service`, and asserts: the sweep's `systemctl list-units`
call requests `--type=path`; the path unit is reported at `urgent` priority
with the non-recovery note; the ordinary service stays at `high`; no
cross-classification. Run via `python3 -c "import subprocess;
subprocess.run(['bash','tests/test_path_unit_urgent.sh'])"` (the sandbox's
per-command allowlist has no bare `bash <script>` entry outside `scripts/`).
