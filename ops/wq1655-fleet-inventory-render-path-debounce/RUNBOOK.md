# wq#1655 — deploy: fleet-inventory-render.path debounce + urgent sweep

Source: `research/FLEET_SERVICE_ATLAS.md` #11/F-7 (wq#1627). See DESIGN.md for
the full rationale.

## 0. Reset the currently-failed unit now

`fleet-inventory-render.path` is `loaded failed failed` right now (confirmed
2026-09-11). This alone does not prevent recurrence — do steps 1-2 first, then
reset:

    systemctl reset-failed fleet-inventory-render.path
    systemctl start fleet-inventory-render.path
    systemctl is-active fleet-inventory-render.path   # expect: active

## 1. Edit the 2 unit files per units.diff

- `/etc/systemd/system/fleet-inventory-render.service` — add
  `StartLimitIntervalSec=0` under `[Unit]`.
- `/etc/systemd/system/fleet-inventory-render.path` — add
  `TriggerLimitIntervalSec=10s` and `TriggerLimitBurst=20` under `[Path]`.

`systemctl cat fleet-inventory-render.service fleet-inventory-render.path`
first to confirm current content matches the pre-image in `units.diff` before
editing (both are read-only and allowlisted).

## 2. Reload and verify

    systemctl daemon-reload
    systemctl cat fleet-inventory-render.service fleet-inventory-render.path \
        | grep -E '^(StartLimitIntervalSec|TriggerLimitIntervalSec|TriggerLimitBurst)='
    systemd-analyze verify /etc/systemd/system/fleet-inventory-render.service
    systemd-analyze verify /etc/systemd/system/fleet-inventory-render.path

Then do step 0's reset-failed/start if not already done.

## 3. Deploy the urgent-priority failed-units sweep

Check first whether `ops/wq1657-guard-exit-convention/staged/lumen-failed-units`
has already been installed to `/home/lumen/bin/lumen-failed-units`
(`grep -c is_finding_exit /home/lumen/bin/lumen-failed-units`):

- **If wq#1657 is already live** (`is_finding_exit` present): diff this
  ticket's `staged/lumen-failed-units` against the live file — they should
  differ only in the `--type=path` addition and the `is_non_recovering`
  urgent-priority branch. Install this ticket's copy directly.
- **If wq#1657 is not yet live**: this ticket's `staged/lumen-failed-units`
  already carries both fixes (it was built on top of wq#1657's staged copy,
  not the plain live one) — install it and treat wq#1657 as landed too, or
  hand-merge if wq#1657's scope has since diverged.

    install -m 0755 staged/lumen-failed-units /home/lumen/bin/lumen-failed-units
    /home/lumen/bin/lumen-failed-units --print   # sanity: should not error

## Verify

    python3 -c "import subprocess,sys; sys.exit(subprocess.run(['bash','tests/test_path_unit_urgent.sh']).returncode)"

All 6 assertions should print `ok`.

## Rollback

`git show HEAD:ops/wq1655-*/staged/lumen-failed-units` vs. whatever was live
before step 3 (or wq#1657's staged copy) holds the prior content. The unit
edits in step 1 are additive single lines — delete them and
`daemon-reload`/`reset-failed` to revert.
