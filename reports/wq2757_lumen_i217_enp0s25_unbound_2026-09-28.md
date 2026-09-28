# wq#2757: why Lumen's onboard I217-LM (enp0s25) has no netdev

Date: 2026-09-28. Read-only diagnostics on Lumen; nothing on the node was changed.

## Verdict

The NIC is healthy and the driver is fine. **`nic-park.service` unbinds it at every boot.**
That unit came from the 2026-08-03 port-parking experiment. The 2026-08-07 fleet-wide
reversal was supposed to remove it everywhere, but on Lumen it survived. The last boot was
2026-08-31 12:15 UTC. At that boot the unit re-parked enp0s25: link down, then
`echo 0000:00:19.0 > /sys/bus/pci/drivers/e1000e/unbind`. The "since 2026-09-10" in the
ticket is only the day wq#1666 first noticed it. The device has actually been unbound since
the 08-31 boot.

This is not a BIOS setting, a missing firmware file, an e1000e probe failure, or a hardware
fault.

## Evidence

| Check | Reading | Meaning |
|---|---|---|
| PCI presence | `/sys/bus/pci/devices/0000:00:19.0` exists, `8086:153a`, subsystem `1028:0617`, class `020000` | The BIOS has the onboard NIC enabled. If it were disabled in setup, 00:19.0 would not enumerate at all |
| Driver binding | `driver` symlink absent, no `net/` dir, `enable=0`, `driver_override=(null)` | Present on the bus but no driver bound. Nothing pins it to another driver |
| Control device | `0000:01:00.0` `10ec:8125` → `r8169`, `net=['ens1']` | Driver binding works normally on this kernel |
| e1000e module | `/sys/module/e1000e` `initstate=live`, `refcnt=0`, in-tree | The module is loaded but owns no device |
| Alias match | modalias `pci:v00008086d0000153Asv00001028sd00000617bc02sc00i00` matches `alias pci:v00008086d0000153Asv*sd*bc*sc*i* e1000e` in `modules.alias` | Without intervention, the kernel would auto-bind e1000e at boot |
| Blacklists / cmdline | No `e1000e`/`00:19.0`/`enp0s25`/`unbind` in `/etc` or `/usr/lib` modprobe.d, udev rules.d, tmpfiles.d, cron.d. `/proc/cmdline` has no `module_blacklist` | No blacklist and no udev rule is involved |
| The culprit | `/usr/local/sbin/nic-park` (mtime 2026-08-03 05:06). Its `park_one()` runs `ip link set down` and then writes the PCI address to `/sys/bus/pci/drivers/$drv/unbind` for any port with no default route, no IPv4 and no carrier | enp0s25 has no cable, so it meets every park guard |
| Unit state | `systemctl show nic-park.service`: `UnitFileState=enabled`, `ActiveState=active/exited`, `ExecMainStartTimestamp=Mon 2026-08-31 12:15:03 UTC`, `ExecMainStatus=0` | The unit ran at the current boot and succeeded |
| Proof it ran the park path | `/etc/systemd/system/nic-park.service` mtime **2026-08-31 12:15:03**, the same second as ExecMainStart. The enable symlink in `multi-user.target.wants/` has mtime 2026-08-03 05:06:03 | nic-park rewrites its own unit file after the scan loop, and only in non-dry-run mode. So the scan ran for real at boot. The unit also keeps itself installed every time it runs |
| Uptime | `up 27 days, 15:57` on 09-28 04:12, so the boot was 2026-08-31 ~12:15 | Only one boot has happened since the 08-07 rebind. That boot is the one that unbound the NIC |

The boot-time kernel and journal lines are gone. The journal starts at 2026-09-25 13:33,
`kern.log*` starts at 09-21, and `/dev/kmsg` is not readable by the lumen user. So there is
no direct capture of the `e1000e` probe at 08-31. The unit-file mtime is the substitute:
it proves the non-dry-run scan executed at that boot.

## How the 08-07 reversal missed Lumen

The minerva fact `fleet.nic_policy` from 2026-08-07 says: "nic-park.service +
/usr/local/sbin/nic-park removed on every node (verified is-enabled=not-found on all 8)".
Lumen falsifies that claim. Both the script and the 08-03 enable symlink are still there.
The same day, Lumen's own `nic_config` fact recorded that enp0s25 was "REBOUND to e1000e
and enumerating". So someone rebound the port by hand, which is a runtime-only change, and
never ran `nic-park --undo`. The next boot parked the port again. The "8 nodes" list in the
reversal most likely did not include Lumen, or its check ran somewhere other than Lumen.
I have not verified which.

## Decision

The fix is to finish the 08-07 reversal on Lumen. That needs root, which this worker lane
does not have, and `systemctl disable` is on the guard deny-list. It does **not** need a
reboot. I filed it as a follow-up ticket with these exact steps:

```
ssh lumen@100.117.66.102 'sudo systemctl disable nic-park.service \
  && sudo rm -f /etc/systemd/system/nic-park.service /usr/local/sbin/nic-park \
  && sudo systemctl daemon-reload \
  && echo 0000:00:19.0 | sudo tee /sys/bus/pci/drivers/e1000e/bind'
```

Do not use `nic-park --undo`. It rebinds every unbound PCI device to e1000e, igb, r8169 and
tg3 by brute force, which is broader than this fix needs.

What happens after the rebind:
- The netdev comes back as `enp0s25` with MAC 64:00:6a:58:fc:d8 (never spoof). It stays
  NO-CARRIER until a cable is plugged in.
- `/run/systemd/network/zzzz-dracut-default.network` matches every non-loopback link with
  `DHCP=yes`, so networkd will manage enp0s25. It will DHCP if the port is ever cabled.
  The DHCP default route gets networkd's default metric of 1024, which is above ens1's 100,
  so ens1 stays primary. That makes enp0s25 a working failover path.
- `systemd-networkd-wait-online` is pinned to `-i ens1` by a drop-in, so an uncabled
  enp0s25 cannot bring back the 08-02 boot stall.

Also needed: a fleet-wide audit for leftover nic-park installs on other nodes. From this
lane, `fleet-exec run` returned `DENY [grant:grant_missing_env]`, so I could not check.
The `fleet.nic_policy` 08-07 fact should get a contradiction note, proposed through Minerva
review. It should not be auto-edited.

## Re-runnable probe (fails once the fix lands)

```
python3 -c "import os;p='/sys/bus/pci/devices/0000:00:19.0';print('bound' if os.path.exists(p+'/driver') else 'UNBOUND')"
systemctl is-enabled nic-park.service   # expect: enabled now; not-found after the fix
```

The control device, `0000:01:00.0`, prints `bound` from the same probe, so the probe does
distinguish the two states.
