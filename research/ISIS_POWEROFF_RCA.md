# ISIS_POWEROFF_RCA — why isis stopped five times between 09-18 and 09-20

wq#2140 · 2026-09-20 · filed by wq#2127 · supersedes the `isis.service_state` note of 2026-09-20 15:40Z

Every number below was read on 2026-09-20 from live instruments: isis's own journald
(`journalctl -b -N`), the applesmc sysfs tree at `/sys/devices/platform/applesmc.768`,
netdata's retained `sensors.*` and `system.cpu` charts on isis (which survive the reboot,
so the thermal trace of a boot that ended badly can be read back without reproducing it),
`/home/lumen/glance/assets/plugs.json`, and `claude_memory.md`. Commands are in §7.

## 0. TL;DR

The hypothesis this ticket was opened to test — *disable-gpus removed both cards, so the SMC
fan no longer ramps, heat builds under load, and the SMC cuts power* — **is falsified.** Four
independent checks kill it. A different, unrelated defect (`disable-gpus.service`) is real and
should still be retired, and the three genuinely abrupt power losses are **not yet explained**
and point at hardware, not software.

| # | Claim under test | Finding | Verdict |
|---|------------------|---------|---------|
| 1 | "the fan no longer ramps since the cards were removed" | isis's fan sat at 789–797 rpm for the whole 09-18 → 09-20 window, **including pre-change hours at 84 % CPU**. There is no "no longer" — it never ramped. | **falsified** |
| 2 | "all three boots end with no stop sequence" | The 09-20 01:04:08 ending is a clean `systemd-poweroff`, triggered by `Power key pressed short` 10 s after a **USB keyboard was hot-plugged**. | **falsified** |
| 3 | "the 09-19 GPU-disable change caused the hard cuts" | Boot -4 ended abruptly at **09-19 10:47:46**, with both GPUs amdgpu-bound and `disable-gpus` never yet run (first activation 09-19 15:13:39). The failure mode predates the change. | **falsified** |
| 4 | "heat builds under load, SMC cuts power" | No thermal, critical-temperature, MCE or throttling entry in any of the three kernel logs. Boot -2 died at **0.4 % CPU**, TC0P ≈ 48 °C. | **falsified** |
| 5 | `disable-gpus.service` is harmful and violates the June standing rule | Confirmed, independently of the crashes: its unbind step fails every boot, and isis idles at **103.2 W vs hathor's 86.6 W** on identical hardware. | **confirmed** |
| 6 | What actually cut power at 09-19 10:47, 09-20 02:47 and 09-20 15:33 | Instantaneous loss, no OS-visible cause, one instance predating every software change under suspicion. Hardware (AC path / PSU / SMC) is the surviving suspect. | **unresolved** |

**Evidence grade.** §1–§4 (falsification): *strong* — direct instrument data plus a pre-change
instance of the same failure. §2 (keyboard): *conclusive* — an explicit logind entry naming the
device. §5 (retire the unit): *strong*. §6 (cause of the abrupt cuts): *weak / hypothesis only* —
no instrument on isis recorded anything, by construction.

**Operational answer to the question that blocked the fleet:** the GPU configuration is not what
is killing isis, so returning it to the June configuration is correct housekeeping but is **not**
a fix for the abrupt cuts. `llama-qwen36` should stay stopped until §6 has a hardware verdict.

## 1. The boot table

`journalctl --list-boots` on isis, plus how each boot ended:

| Boot | Ran | Ended | Ending |
|------|-----|-------|--------|
| -5 | 08-31 08:44:46 → 09-18 05:31:36 | 05:31:36 | clean `systemd-poweroff` (18 days uptime, June config) |
| -4 | 09-18 11:44:27 → 09-19 10:47:46 | 10:47:46 | **abrupt** — last line is the routine 30 s `vram-textfile` run |
| -3 | 09-19 15:13:25 → 09-20 01:04:08 | 01:04:08 | clean `systemd-poweroff` — **spurious USB power key** (§2) |
| -2 | 09-20 01:06:24 → 09-20 02:47:40 | 02:47:40 | **abrupt**, at 0.4 % CPU |
| -1 | 09-20 15:13:53 → 09-20 15:33:53 | 15:33:53 | **abrupt**, llama-server serving |
| 0 | 09-20 15:39:55 → now | — | idle, `llama-qwen36` stopped on purpose |

Boot -5 is the control everyone forgot: **18 unbroken days on the June configuration**, ending in a
commanded poweroff. `disable-gpus.service` was created 09-19 09:08:23 and first ran 09-19 15:13:39.
Boot -4's abrupt ending sits between those two facts, which is what kills claim 3.

## 2. The 09-20 01:04 ending was a keyboard, not a thermal cut

Boot -3, verbatim:

```
01:03:57 kernel: usb 1-3: new low-speed USB device number 2 using xhci_hcd
01:03:57 kernel: usb 1-3: New USB device found, idVendor=0461, idProduct=4e67
01:03:57 kernel: usb 1-3: Product: HP USB Multimedia Keyboard
01:03:57 systemd-logind: Watching system buttons on /dev/input/event7 (HP USB Multimedia Keyboard)
01:03:57 systemd-logind: Watching system buttons on /dev/input/event6 (HP USB Multimedia Keyboard)
01:04:07 systemd-logind: Power key pressed short.
01:04:07 systemd-logind: Powering off...
01:04:08 systemd[1]: Finished systemd-poweroff.service - System Power Off.
```

A keyboard was plugged into a headless box ten seconds before its power key fired. The machine
then came back at 01:06:24 — the MacPro6,1 auto-powers-on after entering G3, which is why this
looked like a crash-and-reboot from the outside.

The same keyboard was still present in boot -2 and disconnected at 01:10:16
(`usb 1-3: USB disconnect, device number 2`). It is **absent** from boot -1's button list, which
contains only `HID 05ac:820a`, `Power Button` ×2 and `Sleep Button`. So the keyboard explains
exactly one ending — 01:04:08 — and cannot explain boots -4, -2 or -1.

The "last journal line is always `vram-textfile`" observation in the ticket is a base-rate
artifact: `vram-textfile.timer` has `OnUnitActiveSec=30s`, so on an idle box it is almost always
the most recent entry.

## 3. The fan never ramped — before or after the change

netdata, isis, hourly buckets (bucket end shown), `sensors.fan_applesmc-isa-0300_fan1_Main_input`
against `system.cpu`:

| Bucket | fan rpm | Te0T | TC0P | TM1a | cpu % | config |
|--------|---------|------|------|------|-------|--------|
| 09-18T23:58 | 789.5 | 81.4 | 52.1 | 64.4 | **84.4** | pre-change, both GPUs bound |
| 09-19T04:24 | 794.9 | 82.0 | 50.6 | 61.8 | 48.2 | pre-change |
| 09-19T07:44 | 796.6 | 84.1 | 51.8 | 62.9 | 68.1 | pre-change |
| 09-19T22:09 | 789.2 | 84.2 | 49.1 | 61.1 | **0.3** | post-change, idle |
| 09-20T02:35 | 789.4 | 82.5 | 47.9 | 60.2 | 0.4 | post-change, idle |

Across the whole 09-18 → 09-20 range the fan stays inside **789.2–796.6 rpm**. It did not ramp at
84 % CPU with both cards bound, and it did not ramp after they were removed. The hypothesis
required a *change* in fan behaviour; there was none. `fan1_min` reads 790 and `fan1_max` 1947 with
`fan1_manual=0`, so the SMC is in auto mode and simply keeps this chassis at its floor.

The controls behave the same way: hathor and nephthys ran 790–846 rpm through 25+ minutes of
12-thread load (wq#2127). All three MacPro6,1 units idle their single fan regardless of load.

The finer trace of boot -1's load, at 30 s buckets, shows the same thing while temperature climbed
22 °C:

```
15:15:00  fan 790.0  Te0T 59.9  TC0P 27.0  cpu  1.7
15:16:00  fan 789.5  Te0T 64.2  TC0P 30.3  cpu 84.0
15:18:00  fan 790.2  Te0T 68.5  TC0P 37.7  cpu 84.0
15:20:30  fan 789.3  Te0T 71.4  TC0P 44.3  cpu 83.8
```

## 4. Nothing thermal is recorded, and two of the three cuts were not under load

`journalctl -b -3|-2|-1 -k` contain no `thermal`, `critical temperature`, `mce` or `throttl`
entry beyond the boot-time governor registrations. The last kernel line before each abrupt
ending is the same benign apparmor denial a `who` poll emits every 62 s.

Load state at each ending:

- boot -2 (02:47:40): netdata `system.cpu` 0.3–0.4 % for the preceding hour. **Idle.**
- boot -1 (15:33:53): llama-server was serving (`srv stop: cancel task, id_task = 11` at 15:33:43)
  and TC0P had risen 46.4 → 52.9 °C over the preceding three minutes. **Under load** — the only
  one of the three that matches the ticket's premise.
- boot -4 (09-19 10:47:46): pre-change, so not attributable to the GPU configuration at all.

Peak temperatures involved (TC0P ≈ 53 °C, Te0T ≈ 84 °C) are below what hathor survived the same
day (Te0T 90 °C under sustained load, still up).

`power-remediate` is ruled out as the actor: it is `ARMED=1`
(`/home/lumen/etc/power-remediate.conf`) and isis is in its `ELIGIBLE` set, but every isis tick
was guard-blocked and it only ever logged `RECOVERED`.

## 5. `disable-gpus.service` is broken and costs power — retire it regardless

This is a separate, confirmed defect. It did not cause the crashes, but nothing about it works as
intended.

**5a. Prior art it violates.** `claude_memory.md:4053`, *CRITICAL REVERSAL 2026-06-25*: gating a
GPU on the Macs where amdgpu binds both D300s overheated the shared MacPro6,1 thermal core; the
fix was to rebind amdgpu and restore `dpm=low` ("temps 54-56C normal"), and the **standing rule**
since then is *leave both GPUs amdgpu-bound with dpm=low on isis/hathor/nephthys/khepri.* The
2026-09-19 unit reintroduces exactly the pattern that rule forbids.

**5b. Half of it has never worked.** The unit's `ExecStart` is

```
for p in 0000:02:00.0 0000:06:00.0; do
  echo -n $p > /sys/bus/pci/drivers/amdgpu/unbind 2>/dev/null
  echo 1 > /sys/bus/pci/devices/$p/remove 2>/dev/null
done
```

and on **every** boot the first write fails:

```
09-19T15:13:39 bash[2014]: /sys/bus/pci/drivers/amdgpu/unbind: Permission denied   (×2)
09-20T01:06:39 bash[2019]: /sys/bus/pci/drivers/amdgpu/unbind: Permission denied   (×2)
09-20T15:14:07 bash[2046]: /sys/bus/pci/drivers/amdgpu/unbind: Permission denied   (×2)
```

Only the PCI `remove` takes effect. The cards leave the bus with amdgpu's `dpm=low` no longer
applied and nothing replacing it — the `gpu-lowpower` knob lives in amdgpu sysfs and disappears
with the device. `ls /sys/bus/pci/drivers/amdgpu/` on isis today lists no bound device.

**5c. It increases draw instead of reducing it.** The unit's stated purpose is "too small for LLM,
idle draw". Measured, same hardware, both idle (`plugs.json`, 2026-09-20T15:56:51Z):

| Node | GPUs | Idle wall |
|------|------|-----------|
| isis (p1) | PCI-removed, unmanaged | **103.2 W** |
| hathor (p3) | amdgpu-bound, dpm=low | **86.6 W** |

**+16.6 W**, against a June-documented baseline of ~78–80 W for this chassis with both cards bound.
The matching idle sensor shift on isis, pre-change vs post-change at comparable CPU:

| | Te0T | TC0P | TM1a |
|---|---|---|---|
| 09-18T21:45, cpu 0.7 % (pre) | 78.8 | 40.9 | 53.2 |
| 09-19T22:09, cpu 0.3 % (post) | 84.2 | 49.1 | 61.1 |
| Δ | **+5.4** | **+8.2** | **+7.9** |

So the unit makes isis hotter and hungrier at idle while delivering nothing. Retire it on the
merits, not as a crash fix.

## 6. What is still unexplained

Three endings — 09-19 10:47:46, 09-20 02:47:40, 09-20 15:33:53 — are instantaneous losses with no
stop sequence, no kernel warning and no thermal event. The first predates every software change
under suspicion. Adjacent facts that belong to the same picture:

- the 09-19 no-POST incident: 10+ minutes with no ping and **no diagnostic lights**, which is a
  power/RAM/board symptom, not a software one (`lumen_state isis.service_state` history);
- isis was moved off plug p5 during that incident (p5 now reads `p5-unoccupied`) and the abrupt
  losses continued on p1, so the plug swap did not fix it;
- `magicsock: network down` on isis's tailscaled from 01:02, and lumen-side ssh probes already
  failing at 00:59 — the box was losing its network before at least one ending.

Surviving hypothesis: an AC-path, PSU or SMC fault in this specific MacPro6,1. It cannot be
narrowed further from software; §7's plan queues the physical test.

## 7. Plan — reversible, no initramfs, no vfio

| # | Action | Reverse by | Owner |
|---|--------|-----------|-------|
| P1 | At the next planned power-on: `systemctl disable --now disable-gpus.service` on isis, reboot so amdgpu binds both cards, re-apply `gpu-lowpower` `dpm=low`. Returns isis to the configuration that ran 18 days unbroken (boot -5). | `systemctl enable --now disable-gpus.service` | ops ticket |
| P2 | Leave no keyboard attached to isis, and make it durable: `HandlePowerKey=ignore` in `/etc/systemd/logind.conf.d/`. Note the tradeoff — a real power-button press then no longer powers off; acceptable on a node that is power-cycled via its plug. | delete the drop-in | ops ticket |
| P3 | Keep `llama-qwen36` stopped and the LiteLLM `local-worker` cooldown in place until P1 has landed **and** isis has held ≥24 h without an abrupt ending. The GPU change is not the cause, so P1 alone does not clear it. | — | this RCA |
| P4 | Retire the same unit on hathor and nephthys (hathor's copy has the wq#2136 quoting bug and is inactive) and correct the Mac blueprint, which still advises disabling GPUs. | re-add | follow-up ticket |
| P5 | **Physical, Frank only:** the three abrupt losses need an AC-side test — move isis to a known-good plug/outlet and watch for recurrence; if it recurs there, suspect the PSU or SMC. Queue as a physical task, not a gate on P1–P4. | n/a | Frank |
| P6 | Separate bug: isis's `remediation_hold` fact is the *string* `"off — cleared 2026-09-18: …"`, and `power-remediate`'s guard treats anything not exactly `off` as an active hold, so isis's automatic power recovery has been silently disabled since 09-18. | fix the guard or the fact value | follow-up ticket |

## 8. Commands

```sh
# boot table and how each boot ended
fleet-exec run isis -- journalctl --list-boots --no-pager -n 8
fleet-exec run isis -- journalctl -b -3 --no-pager -o short-iso --since 01:03 --until 01:04:09
fleet-exec run isis -- journalctl -b -4 --no-pager -o short-iso -n 6
fleet-exec run isis -- journalctl -b -2 -k --no-pager -o short-iso --since 02:30

# the unit and its per-boot failure
fleet-exec run isis -- systemctl cat disable-gpus
fleet-exec run isis -- journalctl --no-pager -u disable-gpus --since 2026-09-17 -o short-iso
fleet-exec run isis -- ls /sys/bus/pci/drivers/amdgpu/

# live SMC temps + fan (ops/smc_probe.py in this repo)
python3 ops/smc_probe.py labels isis
python3 ops/smc_probe.py sample isis TA0P TC0P Te0T TG0D TG1D TM1a TM2a Tp0T

# retained thermal history across a boot that ended badly (ops/netdata_window.py)
python3 ops/netdata_window.py 100.100.129.24:19999 1789689600 1789873200 46 \
  sensors.fan_applesmc-isa-0300_fan1_Main_input=fan \
  sensors.temperature_applesmc-isa-0300_temp98_Te0T_input=Te0T \
  sensors.temperature_applesmc-isa-0300_temp11_TC0P_input=TC0P \
  sensors.temperature_applesmc-isa-0300_temp74_TM1a_input=TM1a system.cpu=cpu

# idle wall power, isis vs the controls
python3 ops/memgrep.py /home/lumen/glance/assets/plugs.json isis 0
```

Sensor index → SMC key is not stable across kernels; `smc_probe.py labels <node>` resolves it
before every read rather than hardcoding `temp98` = `Te0T`.

## 8a. P6 resolved — wq#2156 (2026-09-20)

The P6 guard bug is fixed in lumen-bin PR #115
(http://100.121.17.5:3000/lumen/lumen-bin/pulls/115, merged; `/home/lumen/bin`
fast-forwarded to 15f15b6, so the live timer runs the fix). `guard_blocked()` in
`power-remediate` compared the whole `remediation_hold` value against the literal
cleared set, so isis's prose fact `"off - cleared 2026-09-18: isis is the WARM
standby LLM by design, MUST stay on+ready to serve the fleet..."` read as an
active hold and every isis tick since 09-18 was guard-blocked
(`/home/lumen/var/power-remediate/power-remediate.log` lines 545-546, 553-554).

The guard now judges only the leading word of the value, per the ticket's
preference for fixing the guard over normalising the fact (the prose-valued
pattern will recur on other nodes):

```python
hold = (facts.get("remediation_hold") or "").strip().lower()
first_word = re.split(r"[^a-z_]+", hold, 1)[0] if hold else ""
if hold and first_word not in ("off", "clear", "cleared", "none"):
    return True, f"remediation_hold={facts['remediation_hold']!r}"
```

`off`/`clear`/`cleared`/`none` as the first word means no hold; any other first
word (`on`, and notably `offline` — token split, not prefix match) still blocks,
fail-safe toward holding. Four regression cases were added to
`power-remediate selftest`, including isis's literal fact value; live selftest
after the fast-forward: 0 FAIL. isis's fact value itself is left as-is — the
prose carries the warm-standby rationale and the guard now reads it correctly.

## 9. Method notes

- No load test was run on isis. It was not needed: netdata's retained series already contain the
  thermal and CPU trace of the boot that died under load, and the falsifying evidence (§3, §4)
  came out of history rather than a new experiment. Re-running the load would have risked another
  power loss on the fleet's warm-standby LLM node, and this worker has no grant that can power it
  back on — `plug-ctl` is not on the build-worker allowlist.
- The hathor/nephthys control figures are quoted from wq#2127; this ticket's grant is `ssh(isis)`
  only, so hathor could not be probed directly (`fleet-exec run hathor` → `no ssh grant`). The one
  control number measured here independently is hathor's idle wall draw, which comes from lumen's
  own plug snapshot and needed no grant.
