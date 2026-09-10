# cron-stamp drop-in generator (wq#1658)

**Problem** (source: `foreman/research/FLEET_SERVICE_ATLAS.md`, wq#1627 §5 item
3 / row F-13): `cron_deadman.py` (wq#1184) only ever alerts on the stamp
files that exist under `/home/lumen/logs/.stamp.*`, and getting a stamp
means someone hand-wired `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp
%n` into that unit. At audit time that was 11 of 118 fleet timers. The rest
— backups, `wadjet-ha-monitor.timer`, sentinel-probes, model-watchdog,
every drift guard — have no dead-man at all: a stopped, masked, or
`unit-drift apply`-clobbered timer produces silence, not an alert.

**Fix.** `lumen_cron_stamp_dropin_gen.py` flips coverage from opt-in to
opt-out. For every *fleet-authored* timer (a real, non-symlink `.timer`
file directly under `/etc/systemd/system` — the same placement test
`unit-drift`'s `scan()` in `/home/lumen/bin/unit-drift` uses to tell fleet
units from distro ones), it resolves the service the timer activates and,
unless that service already has stamp coverage (inline `ExecStartPost` or
an existing drop-in mentioning `lumen-cron-stamp`) or carries the opt-out
tag, generates `<service>.service.d/99-cron-stamp.conf`:

```
[Service]
ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n
```

Opting **out** is a one-line tag in the service unit's `[Service]` section:

```
# lumen-cron-stamp: opt-out
```

`cron_deadman.py` already derives each unit's expected cadence from
`systemctl list-timers --all` (`next` minus `last`), so a new drop-in
lands in the existing alert path with zero changes there — coverage
becomes total with no per-unit cron_deadman work (see
`ops/wq1184-gatus-health-checks/cron_deadman.py:33-58`).

**Verified against the live fleet in `plan` mode** (read-only, no writes):
132 fleet timers currently loaded, 8 already covered, 2 skipped
(`lumen-foreman-gate@nightly.service` / `...@window.service` — templated
`@`-instance units the timer's `Unit=` line names but that don't exist as a
standalone file; already covered by hand today, left alone rather than
guessed at), 122 uncovered. Run `python3
lumen_cron_stamp_dropin_gen.py plan` to reproduce.

## Wiring into `unit-drift snapshot`

`unit-drift` (wq#1041, `/home/lumen/bin/unit-drift`) already runs on a
timer and is the natural place to close this gap automatically: add one
guarded call at the top of `cmd_snapshot()` in `/home/lumen/bin/unit-drift`:

```python
def cmd_snapshot():
    subprocess.run(["/home/lumen/bin/lumen-cron-stamp-dropin-gen", "apply"])
    ...
```

so every baseline snapshot first closes any new dead-man coverage gap
(idempotent — units already covered or opted out are no-ops), then the
existing scan captures the resulting drop-ins into the git baseline like
any other drop-in. `unit-drift check`/`apply` stay exactly as read-only as
they are today (wq#1041's contract) — only `snapshot` gains a write, and
only of this one narrow, additive, non-restarting kind (`ExecStartPost=-`,
leading `-` makes a stamp failure non-fatal to the unit itself, same as the
11 hand-wired units already run in production).

## Deploy

```
install -m755 lumen_cron_stamp_dropin_gen.py /home/lumen/bin/lumen-cron-stamp-dropin-gen
```

then patch `/home/lumen/bin/unit-drift` as above. Run once by hand first
(`lumen-cron-stamp-dropin-gen apply`, as root — writes
`/etc/systemd/system/*/99-cron-stamp.conf` for the 122 currently-uncovered
units and a single `systemctl daemon-reload`) to land the initial 107→118
coverage jump described in the ticket, then let `unit-drift snapshot`
keep it there for units added later. This step was not run against
production from this build session — see `install.sh` and done-notes for
why (blast radius: every fleet timer at once; scoped as a follow-up ops
step, not withheld pending approval).

The live `/home/lumen/bin/unit-drift` + `/home/lumen/bin/handoff` wiring
described above is implemented and open for review/merge in
`lumen/lumen-bin` PR #88 (branch `feat/wq1658-cron-stamp-dropin-gen`):
http://100.121.17.5:3000/lumen/lumen-bin/pulls/88

## Handoff verify lines

`handoff-verify-snippet.sh` in this directory is the LastTrigger-age check
for `wadjet-ha-monitor.timer` and `lumen-backup-critical.timer` the ticket
asks for, meant to be spliced into `verify()` in `/home/lumen/bin/handoff`
(its own repo, committed directly per that repo's convention — see
`ops/wq1294-session-handoff/README.md`). Both of those units matter most
under exactly the failure this ticket targets (a masked/stopped timer is
silent), so they get an explicit handoff line instead of waiting on the
generic dead-man rollout.
