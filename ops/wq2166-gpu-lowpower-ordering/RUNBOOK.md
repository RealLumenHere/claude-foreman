# wq#2166 — gpu-lowpower ordering fix (hathor, nephthys)

## What was wrong

hathor's `gpu-lowpower.service` wrote the knob inline:

```
ExecStart=/bin/bash -c 'for f in /sys/class/drm/card*/device/power_dpm_force_performance_level; do echo low > "$f" 2>/dev/null || true; done; ...; exit 0'
```

Nothing ordered that after the cards existed. Measured on hathor:

| boot | kernel | multi-user.target | card1 (0000:02:00.0) | card2 (0000:06:00.0) | outcome |
|---|---|---|---|---|---|
| 2026-08-31 08:45 | 7.0.0-27 | 139.3s | 19.7s | 20.7s | won by 119s |
| 2026-09-19 15:13 | 7.0.0-31 | 17.8s | 18.2s | 19.2s | lost by 0.4s / 1.3s |

The unit never had an ordering guarantee — the August boot was simply 121s slower, and
the cushion hid the bug. When the glob matched nothing bash passed the literal pattern
through; `2>/dev/null` does not suppress it, because bash reports a failed redirection on
its own stderr before the redirection list is complete. `|| true` and `exit 0` then made
`Type=oneshot` report success: `is-active=active`, `ExecMainStatus=0`, both D300s at
`auto`/`balanced` for a day.

nephthys survived the same race only because it runs a script (v3, 2026-07-31) with a
wait loop. Its unit started at 18.7s, before card2 finished at 19.5s.

## What this bundle ships

| file | lands at | role |
|---|---|---|
| `99-gpu-lowpower.rules` | `/etc/udev/rules.d/` | starts `gpu-lowpower@cardN.service` when an amdgpu card appears — the ordering fix |
| `gpu-lowpower@.service` | `/etc/systemd/system/` | per-card applier, `After=`/`BindsTo=` that card's `.device` unit |
| `gpu-lowpower.service` | `/etc/systemd/system/` | boot reconciler; re-applies, reads back, exits non-zero if any card is not `low` |
| `gpu-lowpower.sh` | `/usr/local/sbin/` | the three modes: `--card cardN`, `--check`, and no-argument reconcile |

Two invariants, both pinned by `ops/tests/test_wq2166_gpu_lowpower.py`:

1. Every loop tests `-e` before it writes, so an unmatched glob is a no-op, never a path.
2. Every write is read back. `gpu-lowpower.service` can no longer be `active` with a card
   at `auto` — the reading that made this defect invisible for a day.

`EXPECTED_CARDS` defaults to 2 (MP6,1 dual D300). Override per node in
`/etc/default/gpu-lowpower`.

## Apply

Needs root on the node. `fleet-exec` cannot do it: `run` is read-only
(`harness/fleet-exec-policy.yaml:180`), the `sudo` verb is v2 and unimplemented (line
207), and a `copy` grant for `hathor:/home/hathor/lumen-drop` was denied to the wq#2166
worker (`DENY [grant:no_copy_dir]`), so the bundle could not even be staged from here.

From a box with ssh to the node:

```
scp -r ops/wq2166-gpu-lowpower-ordering hathor:/tmp/
ssh hathor 'sudo bash /tmp/wq2166-gpu-lowpower-ordering/install.sh'
```

`install.sh` refuses to run as non-root, refuses a node where fewer than
`EXPECTED_CARDS` cards expose the knob (`FORCE=1` overrides), and finishes by re-reading
every card — it exits non-zero rather than claiming success.

Then, from Lumen:

```
bash ops/wq2166-gpu-lowpower-ordering/install.sh --verify
```

Read-only. Exit 0 only when every card on hathor and nephthys reads `low`/`battery`, the
unit is `active` with `ExecMainStatus=0`, no `gpu-lowpower@` instance is failed
(wq#2852), and all four files byte-match this bundle.
Until the apply lands it exits 1 — that is the alarm this ticket existed to create, and
`ops-deploy-drift` will keep one `ops-drift:wq2166-gpu-lowpower-ordering` row open until
someone clears it.

nephthys is in `NODES` too. It is correct today on v3, so `--verify` will flag it as
drifted from the bundle until it is upgraded; apply the same two commands there.

## wq#2852: failed instance after boot (nephthys, 2026-09-27)

`gpu-lowpower@card1.service` ended `killed, status=15/TERM ... Failed with result
signal` while the reconciler passed and both cards read low. There was no card removal.

Cause: the rule matched `ACTION=="add"` only. udev re-runs every rule on each uevent,
and amdgpu sends `change` uevents on the card minor (drm hotplug). On that event the
device had no `systemd` tag and no alias. PID1 checks the *current* tag on every
non-remove uevent (systemd v255 `src/core/device.c:762-769`) and clears the device's
found bit when it is missing (`:1196-1199`). So the `.device` unit went dead and
`BindsTo=` stopped the instance. If the instance is still in `apply_card`'s
`CARD_WAIT_SECS` knob wait, the stop kills it and the unit fails. If it has already
exited, the stop is clean, and the instance goes `inactive` without an error.

Fix: the rule matches `ACTION!="remove"`, the same pattern as upstream `99-systemd.rules`
for net/bluetooth. `BindsTo=` stays because a real card removal should still stop the
instance. `SuccessExitStatus=SIGTERM` was rejected: it would hide the spurious stop,
not prevent it. `--verify` now fails on any failed `gpu-lowpower@` instance, and the
install step runs `reset-failed` on stale instances before it re-triggers.

Live check on a node after apply. This needs no reboot. The trigger must be `change`:
`--action=add` re-matched the old rule too, so it cannot reproduce the bug.

```
ssh <node> 'systemctl is-active sys-subsystem-drm-devices-card1.device gpu-lowpower@card1.service'
ssh <node> 'sudo udevadm trigger --action=change /sys/class/drm/card1 && sleep 2 && systemctl is-active sys-subsystem-drm-devices-card1.device gpu-lowpower@card1.service; systemctl --failed --no-legend'
```

The old rule prints `inactive` for both units after the trigger. The fixed rule prints
`active` for both, and the failed list is empty.

## Reversibility

```
ssh <node> 'sudo rm -f /etc/udev/rules.d/99-gpu-lowpower.rules /etc/systemd/system/gpu-lowpower@.service && sudo udevadm control --reload && sudo systemctl daemon-reload'
```

That drops the udev path and leaves `gpu-lowpower.service` + `gpu-lowpower.sh`, which is
nephthys's proven v3 shape plus a read-back. To go all the way back to the pre-wq#2166
unit, restore the `ExecStart=/bin/bash -c ...` one-liner recorded at the top of this file
— but note that it is the defect, not a baseline.

## Downstream

wq#2140's RCA measured hathor's 86.6 W idle control while hathor was at `auto`, not
`low`. The number needs retaking after this lands; the isis gap (103.2 W) can only widen.
