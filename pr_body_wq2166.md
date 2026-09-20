## What

A new ops bundle, `ops/wq2166-gpu-lowpower-ordering/`, that makes the dual-D300 Mac Pro
nodes apply their low-power GPU setting when the cards appear rather than whenever
`multi-user.target` happens to be reached, and that fails visibly when it does not.

## Why

hathor's `gpu-lowpower.service` has read `active` with `ExecMainStatus=0` since
2026-09-19 while both D300s sat at `power_dpm_force_performance_level=auto` and
`power_dpm_state=balanced`. The fleet's headline GPU power control was green and
applying nothing for a day.

The unit wrote the knob inline:

```
ExecStart=/bin/bash -c 'for f in /sys/class/drm/card*/device/power_dpm_force_performance_level; do echo low > "$f" 2>/dev/null || true; done; ...; exit 0'
```

Nothing ordered it after the cards existed. Monotonic timestamps from hathor:

| boot | kernel | multi-user.target | card1 | card2 | unit start |
|---|---|---|---|---|---|
| 2026-08-31 | 7.0.0-27 | 139.337s | 19.685s | 20.657s | 139.342s |
| 2026-09-19 | 7.0.0-31 | 17.837s | 18.216s | 19.157s | 17.845s |

The cards enumerate at the same second on both boots. What moved is multi-user.target:
139.3s → 17.8s. The ticket read this as "the DRM nodes do not exist yet, this is new" —
in fact the unit never had an ordering dependency and had been winning a race on a slow
boot by 119 seconds. On the fast boot the glob matched nothing, bash passed the literal
pattern through, and `|| true` + `exit 0` reported success. `2>/dev/null` did not hide
the error because bash reports a failed redirection before reaching it.

nephthys survived the same race — its unit also started before card2 appeared — only
because it runs a script with a wait loop that hathor was never given. The fleet has been
running two different implementations of the same control.

## How

- `99-gpu-lowpower.rules` — udev tags each amdgpu DRM card and pulls in
  `gpu-lowpower@cardN.service` as the card appears. This is the ordering fix; no sleep is
  the mechanism.
- `gpu-lowpower@.service` — per-card applier, `After=`/`BindsTo=` that card's `.device`
  unit.
- `gpu-lowpower.service` — stays the headline unit and becomes a reconciler: it
  re-applies, reads every knob back, and exits 1 on a mismatch. It is also the backstop
  on a node without the udev rule. `is-active` can no longer be green while a card is at
  `auto`.
- `gpu-lowpower.sh` — tests `-e` before every write, so an unmatched glob is a no-op
  rather than a filename, and never reports success on an unverified write.
- `install.sh` — the wq#1696 `--verify` contract (read-only ssh probe of hathor and
  nephthys) plus a root install path that re-reads every card before exiting.

## Testing

`python3 ops/tests/test_wq2166_gpu_lowpower.py` — 19 tests, all pass. No node is
contacted: the applier runs against a fake DRM tree via `DRM_ROOT`, the `--verify` cases
stub `ssh` on `PATH`. The three that carry the ticket:

- zero cards — exit 1, and the tree is still empty afterwards (the old failure mode);
- late card — card2 is created 2s into the run and is still applied (hathor's 1.3s miss);
- `test_hathor_as_found_on_2026_09_20_fails` — feeds `--verify` the exact live reading
  taken today (`active`, `ExecMainStatus=0`, both cards `auto`, no files installed) and
  requires exit 1.

Plus read-back catching a write that did not stick, an unwritable knob, `--check` writing
nothing, per-card mode touching only its card, optional `power_dpm_state`, unknown
argument exiting 2, and five `--verify` drift cases.

Not run: `install.sh --verify` against the live nodes. Bare `ssh` and `bash` are not on
this worker's allowlist and `fleet-exec` rejects the composed probe
(`DENY [argv:argv_metachar]`); every reading it would take was taken component-wise with
individual `fleet-exec run` calls instead, recorded in the report.

## Risk / rollback

Nothing in this PR touches a live node. No fleet-exec verb can write to one: `run` is
read-only (`harness/fleet-exec-policy.yaml:180`), the privileged verb is v2/unimplemented
(line 207), and `copy` was denied on an ssh-only grant (`DENY [grant:no_copy_dir]`), so
the bundle could not even be staged. Apply is two commands in the RUNBOOK; filed as
wq#2178 with `ops_verified(wq2166-gpu-lowpower-ordering)` as its liveness probe. wq#2179
retakes the wq#2140 idle-wall control, which was measured with hathor at `auto`. wq#2180
checks khepri and isis, which this ticket's grant could not reach.

`install.sh --verify` exits 1 until wq#2178 lands, so `ops-deploy-drift` will hold one
`ops-drift:wq2166-gpu-lowpower-ordering` row open. That is the alarm this ticket existed
to create, not a regression.

Rollback once applied: remove the udev rule and the template unit, reload udev and
systemd. That leaves `gpu-lowpower.service` plus the script, which is nephthys's proven
shape with a read-back added. Full steps in `ops/wq2166-gpu-lowpower-ordering/RUNBOOK.md`.

Evidence: `reports/2166_gpu_lowpower_ordering.md`.

Refs: wq#2166
