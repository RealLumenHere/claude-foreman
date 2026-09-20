> **HOLD - do not install (wq#2140, 2026-09-20).** isis is the only Mac where this unit actually ran, and isis now cuts its own
> power under inference load (3 abrupt boot endings in 24 h). hathor and nephthys carry the broken copy, never ran it, still have both
> GPUs on the bus, and are stable under the same load. Installing the corrected unit below would put the two healthy boxes into the isis
> configuration. Wait for the wq#2140 RCA verdict. The unit is currently `disabled` on hathor and nephthys on purpose.

# wq#2136 — hathor disable-gpus.service unbalanced quoting

## Finding (read-only, 2026-09-20)

- hathor `/etc/systemd/system/disable-gpus.service:7`: the `for p in 0000:02:00.0` / `0000:06:00.0; do ...` list is split across two lines inside the single-quoted `bash -c` argument. systemd reports `Unbalanced quoting, ignoring`, the unit loads as `bad-setting`, and it has never run (`Active: inactive (dead)`, `is-enabled: enabled`).
- isis has the same unit with the loop on one line (correct). nephthys has the same broken two-line form as hathor.
- hathor GPU state is not the intended one: `/sys/bus/pci/devices/0000:02:00.0` and `0000:06:00.0` still exist, both are bound under `/sys/bus/pci/drivers/amdgpu`, and `/sys/class/drm` has card1/card2 with DP connectors.

## Fix

`disable-gpus.service` in this directory: identical to hathor's unit except the loop list is on one line. Same text applies to nephthys.

## Apply (no power action)

Per node (hathor, nephthys), as root:

    install -m 0644 disable-gpus.service /etc/systemd/system/disable-gpus.service
    systemd-analyze verify /etc/systemd/system/disable-gpus.service
    systemctl daemon-reload

`systemd-analyze verify` must print no `Unbalanced quoting`. Running `systemctl start disable-gpus.service` hot-removes the GPUs from the PCI bus (no reboot); it takes effect on the next boot regardless because the unit is already enabled. Confirm after start: `ls /sys/bus/pci/devices` has no `0000:02:00.0` / `0000:06:00.0`.
