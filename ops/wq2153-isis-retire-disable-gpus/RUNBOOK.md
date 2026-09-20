# wq#2153 — isis: retire `disable-gpus.service`, ignore the logind power key

Executes P1 + P2 from `research/ISIS_POWEROFF_RCA.md` §6 (merged PR #708). Both are
reversible; each step below names its reversal.

**This runbook was written, but not applied, by the wq#2153 build worker.** Both
changes need root on isis plus a node restart, and the foreman worker harness denies
exactly those verbs (`harness/guard.sh:89-94` blocks `sudo|reboot|shutdown|poweroff`
and `systemctl stop|disable|mask` anywhere in a command string, including inside
`ssh host '...'` quoting). The worker's broker grant is read-only `run` on isis
(`fleet-exec grant` -> `verbs: [run]`; `harness/fleet-exec-policy.yaml:162-196`), and
the broker's privileged verbs (`sudo`, `power`) are unimplemented v2. See ISSUE-LOG
#473. Everything below the "Apply" heading needs a privileged operator or a widened
grant; the evidence and the drop-in file are done.

## Evidence (live on isis, 2026-09-20 ~16:07 UTC, read-only via `fleet-exec run isis`)

| claim | check | result |
|---|---|---|
| the unit is still on | `systemctl is-enabled disable-gpus.service` | `enabled` |
| both D300s are off the bus | `ls /sys/bus/pci/drivers/amdgpu/` | only `bind module new_id remove_id uevent unbind` — no `0000:02:00.0`, no `0000:06:00.0` |
| `dpm=low` has nothing applying it | `systemctl is-active gpu-lowpower.service` | `failed` |
| …and why | `journalctl -b -u gpu-lowpower.service` | `15:42:11 ONLY 0/2 amdgpu cards after 120s — writing what exists, FAILING loudly` / `FAIL: wrote=0 expected=2 not-low=0` |
| P2 not yet applied | `ls /etc/systemd/logind.conf.d/` | `No such file or directory` |
| power key still armed | `/etc/systemd/logind.conf:28` | `#HandlePowerKey=poweroff` (stock default in force) |
| long press already safe | `/etc/systemd/logind.conf:29` | `#HandlePowerKeyLongPress=ignore` |
| the HP keyboard is gone… | `journalctl -b -u systemd-logind --grep "Watching system buttons"` | `event3 (HID 05ac:820a)`, `event2/event0 (Power Button)`, `event1 (Sleep Button)` — no `0461:4e67` |

The `gpu-lowpower.service: failed` line is the live confirmation of RCA §5b: the
unit's unbind half fails, only the PCI `remove` lands, and the `dpm=low` knob then
has no device to write to.

**`HID 05ac:820a` matters for P2.** A button-capable HID is *already* enumerated on
isis with no HP keyboard attached, so "leave no keyboard attached" is not on its own
sufficient — the drop-in is the part that actually closes this.

## Apply

P1 — retire the unit. `gpu-lowpower.service` is already `enabled` and its
`ExecStart` waits up to 120 s for amdgpu enumeration before writing `low`
(`/usr/local/sbin/gpu-lowpower.sh`), so **P1 needs no manual `dpm=low` step**: once
the cards are back on the bus it re-applies itself on boot.

    ssh lumen@isis "sudo systemctl disable --now disable-gpus.service"
    ssh lumen@isis "sudo systemctl reboot"

P2 — ignore the power key. Copy `90-ignore-power-key.conf` from this directory:

    ssh lumen@isis "sudo mkdir -p /etc/systemd/logind.conf.d"
    # scp 90-ignore-power-key.conf to /etc/systemd/logind.conf.d/
    ssh lumen@isis "sudo systemctl restart systemd-logind"

Order does not matter; P2 takes effect without a restart.

## Verify

    systemctl is-enabled disable-gpus.service     # -> disabled
    ls /sys/bus/pci/drivers/amdgpu/               # -> 0000:02:00.0 and 0000:06:00.0 present
    systemctl is-active gpu-lowpower.service      # -> active
    grep -H . /sys/class/drm/card*/device/power_dpm_force_performance_level   # -> low, low
    busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
      org.freedesktop.login1.Manager HandlePowerKey                          # -> "ignore"

Power draw is the outcome to confirm, not just the unit states: isis idled at
**103.2 W vs hathor's 86.6 W** on identical hardware (RCA §5c). Expect isis to fall
back toward hathor once both cards are amdgpu-bound at `dpm=low`.

## Rollback

    ssh lumen@isis "sudo systemctl enable --now disable-gpus.service"    # reverses P1
    ssh lumen@isis "sudo rm /etc/systemd/logind.conf.d/90-ignore-power-key.conf \
      && sudo systemctl restart systemd-logind"                          # reverses P2

## Do NOT do on the back of this ticket

Do not restart `llama-qwen36` for fleet traffic. RCA P3 requires the unit retired
**and** isis holding 24 h without an abrupt ending first, and the abrupt-cut cause is
still unresolved (sibling hardware ticket).

That caution is load-bearing as of today — `journalctl --list-boots` shows the cuts
are ongoing, two more since the RCA was written:

    -2  2026-09-20 01:06:24 -> 02:47:40
    -1  2026-09-20 15:13:53 -> 15:33:53
     0  2026-09-20 15:39:55 -> (up)

For contrast, boot -5 — the configuration P1 restores — ran **2026-08-31 08:44:46 ->
2026-09-18 05:31:36, 18 days unbroken**.
