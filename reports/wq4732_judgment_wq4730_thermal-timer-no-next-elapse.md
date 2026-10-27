# wq#4732 escalated judgment: wq#4730 / PR #2524 (root-apply wq#2484 thermal-backpressure)

Judge: opus-5@high, judgment only (no patch, no unit restarts). Date: 2026-10-27.
Subject: `reports/wq4730_root-apply_wq2484-thermal-backpressure.md` and the live state of
`lumen-thermal-backpressure.{timer,service}` on Lumen after that apply.

## Claim under judgment

Stated so it can be proven false:

> The wq#2484 thermal-backpressure guard is live and running on Lumen. The timer that
> `ops/wq2484-thermal-backpressure/install.sh` enables will fire `lumen-thermal-backpressure.service`
> periodically, and each run executes the hwmon-based guard code that wq#4714 rebased.

The claim is false if the timer is enabled and active in systemd's bookkeeping but has no future
elapse scheduled, and the service has not executed since the apply.

The narrower claim that `install.sh --verify` prints `VERIFY OK` (exit 0) is a separate question.
It is true, and the parent verifier reproduced it. The question here is whether `VERIFY OK` means
"live".

## Evidence

All live commands were run read-only through
`/home/lumen/bin/lumen-run start --name wq4732-judge-<x> -- bash -c "<cmd>"`, then
`lumen-run wait <unit> 30` and `lumen-run log <unit>`. Units used:
`lumen-job-wq4732-judge-timer-1793093307`, `lumen-job-wq4732-judge-journal-1793093308`,
`lumen-job-wq4732-judge-stamp-1793093335`. All three returned `DONE:0`.

### What `--verify` checks (`ops/wq2484-thermal-backpressure/install.sh:13-30`, `ops/_verify.sh`)

| Check | Helper | What it actually tests |
|---|---|---|
| service unit file | `vunit_file` -> `vfile` | `/etc/systemd/system/...service` byte-equal to repo (or superseded) |
| timer unit file | `vunit_file` -> `vfile` | same, for the timer |
| timer enabled | `venabled` | `systemctl is-enabled` in `enabled\|static\|alias` |
| timer active | `vactive` | `systemctl is-active` in `active\|activating` |
| guard code | `vcontains` x2 | `^def sample_temps\(` and `poll_budget_s` grep in `ops/thermal_backpressure.py` |
| guard config | `vcontains` | `^  poll_budget_s:` grep in `ops/thermal_backpressure.yaml` |

None of these reads `NextElapseUSecMonotonic`, the timer's `SubState`, `list-timers` NEXT, the
service's `ExecMainStartTimestamp`, or the journal. A timer in `SubState=elapsed` still reports
`is-active` = `active` (`_verify.sh:68-72`), so `vactive` passes on a timer that will never fire again.

### Timer unit as shipped (`ops/wq2484-thermal-backpressure/lumen-thermal-backpressure.timer`)

```
[Timer]
OnBootSec=3min
OnUnitActiveSec=1min
Persistent=true
```

There is no `OnCalendar=`, `OnActiveSec=` or `OnStartupSec=`.

### Live timer state (captured `Tue Oct 27 09:28:27 AM UTC 2026`)

`systemctl show lumen-thermal-backpressure.timer -p NextElapseUSecMonotonic -p NextElapseUSecRealtime
-p LastTriggerUSec -p LastTriggerUSecMonotonic -p TimersMonotonic -p TimersCalendar -p ActiveState
-p SubState -p UnitFileState -p ActiveEnterTimestamp -p ActiveEnterTimestampMonotonic -p Persistent`:

```
ActiveState=active
SubState=elapsed
UnitFileState=enabled
ActiveEnterTimestamp=Tue 2026-10-27 09:16:04 UTC
ActiveEnterTimestampMonotonic=1133748758082
TimersMonotonic={ OnUnitActiveUSec=1min ; next_elapse=0 }
TimersMonotonic={ OnBootUSec=3min ; next_elapse=3min }
NextElapseUSecRealtime=
NextElapseUSecMonotonic=infinity
LastTriggerUSec=Thu 2026-10-08 07:08:20 UTC
LastTriggerUSecMonotonic=0
Persistent=yes
```

`systemctl list-timers lumen-thermal-backpressure.timer --all --no-pager`:

```
NEXT LEFT LAST                        PASSED UNIT                             ACTIVATES
-       - Thu 2026-10-08 07:08:20 UTC      - lumen-thermal-backpressure.timer lumen-thermal-backpressure.service
```

`systemctl show lumen-thermal-backpressure.service -p ActiveState -p Result -p ExecMainStartTimestamp
-p InactiveExitTimestamp -p NRestarts`:

```
ActiveState=inactive
InactiveExitTimestamp=
Result=success
NRestarts=0
ExecMainStartTimestamp=
```

Empty `InactiveExitTimestamp` and `ExecMainStartTimestamp` mean the service has not started once in
the current boot.

Boot and versions: `cat /proc/uptime` = `1134491.86` s (about 13.1 days). `uptime -s` =
`2026-10-14 06:20:15`. `systemctl --version` = `systemd 259 (259.5-0ubuntu3.4)`. The timer's monotonic
activation (`1133748758082` us, about 1,133,749 s since boot) matches `ActiveEnterTimestamp` 09:16:04
today, roughly 13 days after `OnBootSec=3min` elapsed.

### Journal (captured `Tue Oct 27 09:28:28 AM UTC 2026`)

`journalctl -u lumen-thermal-backpressure.service --no-pager --since '2026-10-27 00:00:00'`:

```
-- No entries --
```

`journalctl -u lumen-thermal-backpressure.service --no-pager -b 0` returned `-- No entries --`, so the
service has not run since the Oct 14 boot.

`journalctl -u lumen-thermal-backpressure.timer --no-pager --since '2026-10-14 00:00:00'`:

```
Oct 27 09:16:04 lumen systemd[1]: Started lumen-thermal-backpressure.timer - Poll node temperatures ...
```

That is the one timer event this boot: `Started`, with no trigger after it.

`journalctl -u lumen-thermal-backpressure.service --no-pager -n 5` shows the last run anywhere:

```
Oct 08 07:09:22 lumen python3[2754700]: nephthys  held=False temp=- ... (RuntimeError: 100.98.74.87:19999: netdata did not answer)
Oct 08 07:09:22 lumen python3[2754700]: khepri    held=False temp=49.9 ...
Oct 08 07:09:22 lumen python3[2754700]: thor      held=False temp=- ... (no netdata source)
Oct 08 07:09:22 lumen systemd[1]: lumen-thermal-backpressure.service: Deactivated successfully.
```

That run used the pre-wq#4714 netdata guard. The hwmon guard has never run under the unit.

### Persisted timer stamp

`ls -l --time-style=full-iso /var/lib/systemd/timers/ | grep -i thermal`:

```
-rw-r--r-- 1 root root 0 2026-10-08 07:08:20.921393000 +0000 stamp-lumen-thermal-backpressure.timer
```

The stamp's mtime matches `LastTriggerUSec` exactly. It was left by the Oct 08 install on the
previous boot (journal boot -1, `2026-10-06 .. 2026-10-14`).

### The wq#4730 report itself

The report's evidence for "live" is `VERIFY OK` from `lumen-job-wq4730-verify-...` and the
`ops_verified` probe, which runs the same `--verify`. Its "Liveness" section names no trigger, no
`list-timers` NEXT, and no service run. The install's own `systemctl list-timers` line
(`install.sh:42`) would have printed `-` under NEXT, but the report does not quote it. The
`sample` / `poll --dry-run` / `status` calls in `install.sh` are direct `python3` invocations, not
service activations, so they create no `OnUnitActiveSec` anchor.

## Verdict

**CONTRADICT.** The timer is enabled and active as far as systemd's bookkeeping goes, but it is in
`SubState=elapsed` with `NextElapseUSecMonotonic=infinity`, and `list-timers` NEXT is `-`. The service
has not started once this boot, including the 12+ minutes after the 09:16:04 enable, against a
1-minute interval. As things stand the guard is not live and will not run until something gives the
timer a new anchor. `VERIFY OK` is a false positive for liveness.

## Reasoning

For the monotonic trigger kinds, systemd 259's `timer_enter_waiting` (src/core/timer.c) works like
this:

1. **`OnBootSec=3min`.** The elapse point is boot + 3 min (Oct 14 06:23), well in the past. Normally
   systemd fires an already-passed boot trigger as soon as the timer starts. A timer enabled late on a
   long-running box does run once right away. That catch-up is skipped when `t->last_trigger` is
   already set: systemd treats the trigger as a spent one-shot and disables it. Here `Persistent=true`
   makes systemd load `stamp-lumen-thermal-backpressure.timer` at start and restore
   `LastTriggerUSec=2026-10-08 07:08:20` from the Oct 08 install on the previous boot. So
   `last_trigger` is set, the boot trigger is in the past, and it gets disabled.
   (`TimersMonotonic` still lists it with `next_elapse=3min`, the stale boot-relative point.)
2. **`OnUnitActiveSec=1min`.** The base is `MAX(service.inactive_exit_timestamp.monotonic,
   last_trigger.monotonic)`. The service has not left inactive this boot, so the first value is 0. The
   stamp restores only realtime, so `LastTriggerUSecMonotonic=0`. A base of 0 means the trigger is
   skipped (`next_elapse=0` in `TimersMonotonic`).
3. With no trigger left, systemd puts the timer in `TIMER_ELAPSED`, shown as `SubState=elapsed` with
   `NextElapseUSecMonotonic=infinity`. That matches the observed state.

This refines the verifier's mechanism, and the verdict stays the same. The verifier wrote that
"systemd does not retroactively fire an already-elapsed monotonic OnBootSec trigger just because the
timer is freshly enabled long after boot." In general it does. What suppressed the catch-up here is
the stale Oct 08 persistence stamp, combined with `Persistent=true`. The man page documents
`Persistent=` as meaningful only for `OnCalendar=`, yet here it still loads the stamp that disables
the monotonic catch-up. So the dead timer comes from a stale stamp plus a schedule that has only
monotonic, never-anchored triggers. It is not a general rule about late enables. A forward-fix needs
to cover both.

On how long this lasts: a reboot would anchor `OnBootSec` again, because the new boot trigger lands
in the future, so the guard would start after the next reboot. A manual `systemctl start` of the
service would anchor `OnUnitActiveSec`. Neither has happened, so the claim "live and running" is
false now. A reboot fixing it later does not make wq#4730's claim true.

## Recommendation

Route this to a forward-fix ticket against `ops/wq2484-thermal-backpressure/`. Do not reopen wq#4714's
guard code; nothing here touches `thermal_backpressure.py`. The fix needs to cover:

1. **Timer spec.** Add a trigger anchored to timer activation, e.g. `OnActiveSec=1min` (or `0`), so a
   fresh `enable --now` always schedules a first run. Drop `Persistent=true`: it does nothing for
   monotonic triggers and is what loaded the stale stamp here. Whoever implements this should test it
   on this box. Re-run `systemctl show -p SubState -p NextElapseUSecMonotonic` and expect
   `waiting` / finite.
2. **Install.** After `enable --now`, either `systemctl start lumen-thermal-backpressure.service` once
   to anchor `OnUnitActiveSec` and prove the hwmon guard runs under the unit, or remove the stale
   `/var/lib/systemd/timers/stamp-lumen-thermal-backpressure.timer` before enabling.
3. **`--verify` contract (`ops/_verify.sh`).** Add a helper, e.g. `vtimer_scheduled UNIT`, that MISSes
   when `SubState != waiting` or `NextElapseUSecMonotonic`/`NextElapseUSecRealtime` are both
   empty/`infinity`. Optionally add a freshness check that the target service's
   `ExecMainStartTimestamp` is within N intervals. `vactive` alone cannot tell `waiting` from
   `elapsed`. Other bundles that `vactive` their timers likely share this blind spot and should be
   swept once the helper exists.
4. **Bookkeeping.** `ops_verified(wq2484-thermal-backpressure)` should not count as SATISFIED until
   (3) lands, since it runs the same `--verify`.

I did not change anything on the live box, the units or `/home/lumen/foreman`.

## Note on process

The `guard.sh` read hook blocked the first read of the wq#4730 report as "already read this session"
(probably the parent verifier's read under a shared session ID). This judge made one ranged read
(offset 1, limit 400), which returned the full 88-line report, and quotes it directly above.
