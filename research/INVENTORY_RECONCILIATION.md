# Fleet inventory reconciliation: inventory YAML vs lumen_state vs live (wq#1666)

Date: 2026-09-10 (probe run 06:5x-07:1x UTC). Session work-1666-022257. Read-only against every node.
Companion PR on fleet-inventory: the YAML corrections listed in section 5 (same ticket).

## 0. Method and evidence grades

Three sources were read for all 25 machines in `fleet/inventory/machines/*.yaml`:

1. **Inventory**: the YAML as of fleet-inventory `fd5404b` (post wq#1580 live-fill).
2. **State**: `lumen_state.py current <node>` plus the raw `state_facts` current slice
   (831 facts, 96 subjects; the 28 subjects relevant here dumped to `/tmp/work-1666-022257/state/`).
3. **Live**: one ssh probe per reachable node (hostname, os-release, uname, `tailscale ip`,
   `ip addr/link`, `lscpu`, `free`, `sudo -n dmidecode -t 0,1,2,17`, `lsblk -d`, `/proc/mdstat`,
   `lspci` VGA, uptime) plus `tailscale status --json` from Lumen for every tailnet device.
   Raw captures: `/tmp/work-1666-022257/live/<node>.txt`.

Reachability at probe time: **13 of 25 probed successfully** (lumen self, anubis, apep, bes, gameboy,
hathor, isis, maat, seshat, sia, sobek, thor, thoth). 2 timed out (khepri, wadjet - both tailnet-offline).
10 have no ssh path by design or by gap (3dprinter, apis, aten, be3600, hapi-1, mirror, nephthys, pbox,
phone, toilet).

Evidence grades used below: **A** = read live this session from the box itself; **B** = read live from
an adjacent system (tailscale status, a neighbour's ARP/route table); **C** = a prior live read recorded
in state_facts with a source_ref; **D** = prose / hand-typed / no source.

Verdict vocabulary: MATCH (all present sources agree), DRIFT (a source is wrong or stale),
GAP (a source is missing the field), CONFLICT (two sources disagree and neither is obviously stale).

## 1. Identity and network matrix (all 25 nodes)

| node | inv status/power | tailnet (B) | ssh probe (A) | TS IP | LAN IP | ssh user | verdict |
|---|---|---|---|---|---|---|---|
| lumen | active/on | self | ok (up 9d) | MATCH | MATCH (state has NO lan_ip fact; it lives inside nic_config prose) | MATCH | GAP(state) |
| anubis | active/on | online | ok (up 9d) | MATCH | MATCH | MATCH (state has no ssh fact) | MATCH |
| apep | active/on | online | ok (up 9d) | MATCH | MATCH (wlp1s0, WiFi) | MATCH | MATCH |
| apis | dead/off | OFFLINE since 08-13 | n/a | MATCH | MATCH | VERIFY everywhere | MATCH |
| aten | bench/VERIFY | no tailnet device | n/a | none | none | VERIFY | MATCH (still provisioning, 51 d) |
| be3600 | active/on | OFFLINE since 06-17 | n/a | MATCH | inv VERIFY; gateway of apep is 192.168.0.1 (B, ARP a8:6e:84:ad:0f:14) | VERIFY | DRIFT(inv power/tailnet claim) |
| bes | active/on | online, tag:lumen-fleet | ok (up 9d) | MATCH | MATCH (enp1s0 MAC matches) | MATCH | MATCH |
| gameboy | active/on | online | ok (up 12d) | MATCH (state has BOTH tailscale_ip and ts_ip) | inv .190; state `192.168.8.190_static_nmcli` -> drift tool reads NO-FACT | MATCH | DRIFT(state value hygiene) |
| hapi-1 | bench/VERIFY | no tailnet device | n/a | VERIFY | VERIFY | VERIFY | MATCH (nothing to verify yet) |
| hathor | active/on | online | ok (up 9d) | MATCH | MATCH (enp12s0) | MATCH | MATCH |
| isis | active/on | online | ok (up 9d) | MATCH | MATCH (enp12s0) | MATCH | MATCH |
| khepri | bench/VERIFY | OFFLINE since 08-15, tag:lumen-fleet | timeout | MATCH | MATCH | MATCH | DRIFT(inv power VERIFY -> off) |
| lumen | see row 1 | | | | | | |
| maat | active/on | online | ok (up 6d) | MATCH | MATCH (enp2s0) | MATCH (maam) | MATCH |
| mirror | decommissioned/off | OFFLINE since 04-03 (windows) | n/a | MATCH | null | n/a | MATCH |
| nephthys | decommissioned/plug-cut | OFFLINE since 08-23, tag:lumen-fleet | n/a | MATCH (state has BOTH tailscale_ip and ts_ip) | MATCH | MATCH | MATCH |
| pbox | decommissioned/off | **last seen 2026-09-10** (windows) | n/a | MATCH | null | n/a | DRIFT(inv power: box is on, just not fleet) |
| phone | active/on | online (android) | n/a | MATCH | inv VERIFY; state has it under subject **s26-ultra**, predicate **lan-ip** = 192.168.8.139 | n/a | GAP(inv) + subject-name split |
| seshat | active/on | online | ok (up 1d3h) | MATCH | MATCH, plus an undeclared SECONDARY dhcp addr 192.168.8.237 on enp175s0 | MATCH | DRIFT(live extra IP) |
| sia | active/on | online, tag:lumen-fleet | ok (up 9d) | MATCH | MATCH | MATCH | MATCH |
| sobek | active/on | online, tag:lumen-fleet | ok (up 9d) | MATCH | MATCH | MATCH | MATCH |
| thor | active/on | online | ok (up 4d17h) | MATCH | MATCH but it is a **DHCP** lease on WiFi wlp45s0 (not static) | MATCH | MATCH (note DHCP) |
| thoth | active/on, known_issue "tailnet-OFFLINE since 09-02" | **online**, last seen 09-09 | **ok** via key /home/lumen/.ssh/thoth_ed25519 (up 1w) | MATCH | MATCH (enp174s0) | MATCH | DRIFT(inv known_issue + state.status both stale) |
| toilet | active/on, monitor:false | online, hostname `toilet-940X5N` (linux), last seen 09-09 | no path | MATCH | MATCH | VERIFY | GAP(inv location: state says Frederick MD) |
| wadjet | standby/off | OFFLINE since 07-19, tag:lumen-fleet | timeout | MATCH | MATCH | MATCH | MATCH |
| 3dprinter | active/on | **OFFLINE since 07-26** (46 d) | no path | MATCH | VERIFY | VERIFY | DRIFT(inv power) |

Tailnet devices with NO inventory file: `DESKTOP-U520OBE` (windows, offline since 03-25), `seneca`
(linux, offline since 04-07 - the pre-rename thoth registration), `GL-BE3600` is be3600. Not fleet;
listed so nobody "discovers" them again. Only 6 devices carry `tag:lumen-fleet` (bes, khepri, nephthys,
sia, sobek, wadjet); the other fleet nodes are user-owned tailnet nodes. Inventory does not model tags.

## 2. Hardware, per node: inventory says / state says / live says

Format per field: `inv | state | live (grade)` -> verdict. Only fields where at least one source has
content are listed; "-" = source has nothing. Serials are the manufacturer serials from DMI; NVMe/SSD
serials from lsblk.

### lumen (Dell Precision Tower 5810) - probed
- make/model/serial/BIOS: `Dell Precision T5810, serial VERIFY, bios VERIFY` | `T5810` | `Dell Inc. Precision Tower 5810, sn FK6FV52, board 0K240Y rev A02, BIOS A34 2020-10-19` (A) -> GAP(inv serial/bios)
- CPU: `E5-2699 v4, 22 cores` | `E5-2699 v4 22C/44T, AVX2 only` | same, 44 threads (A) -> MATCH
- RAM: `60 GB DDR4-ECC, speed VERIFY, ceiling 256` | `64GB 4x16 DDR4-2667 single-rank, runs 2400` | `4x16 GB DDR4 2667 MT/s Micron 18ASF2G72XF12G6V21A in DIMM1-4, DIMM5-8 empty, MemTotal 62157 MiB` (A) -> **DRIFT(inv)**: 60 is `free -g` rounding, not the module total. State is right. dimms_used 4 / total 8.
- Storage: `[sata 1.8T]` | `9 drives: BOOT nvme4 970 PRO sn S5JYNS0RB07557V; md0 RAID5 3x256 PM951 nvme0/1/2; md1 RAID0 4x512 INACTIVE 3/4; sda Patriot Burst Elite 240G -> /mnt/old_lumen` | `9 drives: 970 PRO 512G sn S5JYNS0RB07557V is now **nvme5n1**; md0 raid5 = nvme0/1/2 (MZFLV256 PM951 x3); md1 raid0 **ACTIVE 4/4** = nvme3/4/6 (MZFLV512) + nvme7 (MZFLW512 PM961); **sda = Seagate ST2000LM015 1.8T sn ZDZRTG6X**, no Patriot present` (A) -> **DRIFT(inv)** lists 1 of 9 drives; **DRIFT(state)** on three points: sda model (the Patriot is gone, a 2TB Seagate is in its place), md1 state (assembled since wq#329), and the /dev/nvmeN names (the boot drive moved nvme4 -> nvme5 across reboots; enumeration order is not stable on this box, the serial is).
- GPU: `[]` | `AMD Embedded Radeon E9170 / Lexa PRO 2GB, PCI 0d:00.0, VIDEO OUTPUT ONLY (Frank directive)` | `0d:00.0 AMD Lexa PRO` (A) -> **DRIFT(inv)**: gpu empty; the standing "never compute on it" directive should be carried as a quirk.
- NICs: `[enp0s25 64:00:6a:58:fc:d8 NEVER SPOOF]` | nic_config 08-07: `ens1 RTL8125 3c:7c:3f:a6:be:dd primary 2.5G; enp0s25 I217-LM 64:00:6a:58:fc:d8 rebound to e1000e, no carrier, unconfigured` | `ens1 3c:7c:3f:a6:be:dd is the only netdev; I217-LM is on PCI 00:19.0 but `enp0s25` does NOT exist as a netdev right now (no driver bound)` (A) -> **DRIFT(inv)**: the only NIC listed is the dormant one; the active 2.5G primary is absent. State nic_config "enumerating" is stale (no netdev today). Frank's never-spoof rule is about the onboard I217-LM MAC and stands regardless; record both NICs, keep the rule on enp0s25.
- OS/kernel: inv has no field (`"OS 26.04"` is parked in known_issues) | state: no kernel fact for lumen | `Ubuntu 26.04 LTS, 7.0.0-30-generic` (A) -> GAP(schema): SCHEMA.md has no os/kernel field at all.
- Power: `psu 685, plug P11` | `P11` | - -> MATCH
- ECC: - | `ram_ecc_suspect DIMM4 SN 2106727E, 3 CE 08-10/11` | not re-read (needs edac dump) -> carry as known_issue in inv.

### anubis (Dell OptiPlex 3090 UFF) - probed
- make/model/serial/BIOS: `VERIFY / VERIFY / VERIFY` | - | `Dell OptiPlex 3090 UFF, sn 1XLN9R3, board 05G6P6 A00, BIOS 1.19.0 2023-03-17` (A) -> GAP(inv+state)
- CPU: `i5-1145G7, 4 cores, 11th gen avx512` | - | same, 8 threads, Iris Xe (A) -> MATCH
- RAM: `30 GB, type VERIFY` | - | `2x16 GB DDR4-2400 Micron 16ATF2G64HZ-2G3E1, both DIMM slots used, MemTotal 31121 MiB` (A) -> **DRIFT(inv)** 30 -> 32
- Storage: `[nvme]` | - | `Kingston OM3PDP3256B-A01 256G NVMe sn 50026B76856E81A8` (A) -> GAP(inv)
- NIC: `[]` | - | `enp2s0 c0:25:a5:d0:66:d1` (A) -> GAP
- Kernel: - | `7.0.0-27 (rebooted 2026-06-30)` | `7.0.0-30` (A) -> **DRIFT(state)** stale
- Plug: `P9` | `P9` | - -> MATCH

### apep (whitebox Apollo Lake) - probed
- make/model: `Whitebox Apollo Lake mini-PC (AMI 'Apollolake'), BIOS 5.12 2020-09-07` | apep.hardware (wq#549 live read 08-13): same + `58GB eMMC + 238GB Samsung MZMTE256 M.2 SATA, WiFi-only, Ubuntu 24.04` | DMI not readable (no dmidecode binary on the box; sudo -n itself works), `N3350 2c, 3745 MiB, sda MZMTE256 238.5G sn S1F1NYAF429841 + mmcblk1 58.2G, wlp1s0 98:8d:46:6e:22:d8 up, enp5s0 50:a4:d0:92:06:5b down, HD Graphics 500, Ubuntu 24.04.4, 6.8.0-138` (A) -> MATCH on hw; **GAP(inv)** storage lists only "sata" (eMMC omitted); **DRIFT(state)** kernel fact says 6.8.0-134.

### bes (Trigkey Green G4) - probed
- make/model/serial/BIOS: `VERIFY` | bes.hw `Trigkey Green G4 N100 16GB (N100 unit-1)`; bes.bios = a settings note, not a version | `Trigkey Green G4, sn BN1003LG20791, BIOS ADLNV104 2023-07-17` (A) -> GAP(inv)
- CPU: `Intel N100, 4, gen VERIFY` | - | N100 4c/4t, UHD ADL-N (A) -> MATCH (gen = Alder Lake-N)
- RAM: `14 GB` | `16GB` | `1x16 GB DDR4-3200 (Controller0 DIMM0), 2nd slot empty, MemTotal 15286 MiB` (A) -> **DRIFT(inv)** 14 -> 16
- Storage: `[nvme]` | - | `generic "PCIe SSD" 476.9G nvme sn BE670738097400817675` (A) -> GAP(inv)
- NIC: `enp1s0 7c:83:34:bd:2e:8c` | same (in lan_ip prose) | same + `wlo1 c4:3d:1a:d7:04:a1` (A) -> MATCH
- OS/kernel: - | `Ubuntu Server 26.04` | `26.04, 7.0.0-30` (A) -> MATCH/GAP

### gameboy (Surface Pro 6) - probed
- make/model/serial/BIOS: `Surface Pro 6, serial VERIFY, bios VERIFY` | `Surface_Pro_6_i5-8350U_8GB_256GB_NVMe` | `Microsoft Surface Pro 6, sn 032633490753, UEFI 239.779.768` (A) -> GAP(inv serial)
- CPU/RAM/GPU: `i5-8350U 4c; 8 GB LPDDR3 soldered; UHD 620` | same | `i5-8350U 4c/8t; 2x4 GB LPDDR3-1867; UHD 620` (A) -> MATCH
- Storage: `NVMe 256GB` | `256GB_NVMe` + ssd_health `KUS030202M 35% worn` | `SAMSUNG KUS030202M-B000 238.5G sn S3VDNY2K885184` (A) -> MATCH
- NIC: `[]` | wifi implied (kiosk_config wifi-powersave-off) | `wlp1s0 b8:31:b5:7e:eb:05` WiFi-only (A) -> GAP(inv)
- Kernel: `6.19.8-surface` (board quirk) | `6.19.8-surface-3 GRUB-pinned` | `6.19.8-surface-3` (A) -> MATCH
- State hygiene: two subjects describe this one box (`gameboy` 18 facts, `gameboy-pc` 4 facts) and `gameboy` carries both `tailscale_ip` and `ts_ip`.

### hathor (Mac Pro 6,1) - probed
- make/model/serial/BIOS: `VERIFY` | - (nothing structured; prose in MAC_POWER_DEPLOYMENT) | `Apple MacPro6,1, sn F5KR806KF9VN, BootROM 474.0.0.0.0 2023-01-13` (A) -> GAP(inv+state). Note isis and nephthys run BootROM 481.0.0.0.0 (state says 481 is the final MacPro6,1 firmware); hathor is one firmware behind. Observation only.
- CPU: `E5-2697 v2, 12 cores` | role fact says 24 threads | `E5-2697 v2 12c/24t` (A) -> MATCH
- RAM: `60 GB, type VERIFY` | - | `4x16 GB DDR3-1866 (Samsung M393B2G70DB0-CMA by part-number hex), DIMM1-4, MemTotal 61960 MiB` (A) -> **DRIFT(inv)** 60 -> 64
- Storage: `[sata]` | - | `APPLE SSD SM0256 233.8G sata sn S23DNYBG389957` (A) -> GAP(inv)
- GPU: `Curacao XT GCN1.1 [1002:6810] GPU-A kept, GPU-B reverted to stock` | gpu_kept_status / gpu_b_status agree | `two VGA controllers enumerate: 02:00.0 and 06:00.0 Curacao XT` (A) -> MATCH (stock = both enumerate; the 06-25 standing rule "never gate GPU-B on amdgpu-both-bound Macs" applies)
- NIC: `[]` | - | `enp12s0 00:3e:e1:c9:07:48 (fleet LAN), enp11s0 00:3e:e1:c9:07:47 unused` (A) -> GAP
- Kernel: - | `7.0.0-27` | `7.0.0-27` (A) -> MATCH
- Status/power: inv `active/on, healthy 09-08` | hathor.status 09-08 `healthy`; hathor.power_plug (08-22) still ends "Hathor remains powered off/parked" | up 9 days (A) -> MATCH on status; the power_plug fact's trailing clause is stale prose inside a still-current fact.

### isis (Mac Pro 6,1) - probed
- make/model/serial/BIOS: `VERIFY` | - | `Apple MacPro6,1, sn F5KQT0GKF9VM, BootROM 481.0.0.0.0 2023-10-07` (A) -> GAP(inv+state)
- CPU: `E5-2697 v2, 12` | - | same 12c/24t (A) -> MATCH
- RAM: `60 GB, VERIFY` | - | `4x16 GB DDR3-1866 DIMM1-4, 61960 MiB` (A) -> **DRIFT(inv)** 60 -> 64
- Storage: `[sata]` | ollama_disk 58G used | `APPLE SSD SM0256 233.8G sn S23DNYBG353025` (A) -> GAP(inv)
- GPU: `VERIFY (runs Qwen3-32B)` | gpu_dpm low both D300s | `two Curacao XT (D300) 02:00.0 + 06:00.0` (A) -> **DRIFT(inv)**: the parenthetical implies the GPU serves the model; Qwen3-32B runs on CPU via ollama (Mac D300s are not an inference target anywhere in state).
- NIC: `[]` | - | `enp12s0 00:3e:e1:c8:27:97, enp11s0 :96 unused` (A) -> GAP
- Kernel: - | `7.0.0-27` | `7.0.0-30` (A) -> **DRIFT(state)**

### khepri (Mac Pro 6,1) - OFFLINE, not probed
- make/model/CPU/RAM/storage/NIC: inv **all VERIFY** | khepri.hw (wq#597 live dmidecode 08-14, grade C): `Mac Pro 6,1, E5-2697 v2 12C/24T, 64GB = 4x16 Hynix HMT42GR7AFR4C-RD DDR3-1866 DIMM1-4, 256GB SM0256G SSUBX blade, enp12s0 MAC 00:3e:e1:c5:75:94, Ubuntu 26.04 7.0.0-27`; khepri.ram same, Frank-stated 08-13 + verified | timeout -> **DRIFT(inv)**: every VERIFY here already has a grade-C answer in state; wq#1580 only filled from live ssh and never fell back to the store.
- Power: inv `VERIFY` | status `OFFLINE per tailscale, last seen 26d` (09-10) | tailnet last seen 2026-08-15 (B) -> **DRIFT(inv)** power -> off. Plug: inv `none` | `NONE (Frank 08-22)` -> MATCH.

### maat (WeiBu ADL-N) - probed
- make/model/serial/BIOS: `mini-PC (WeiBu ADL-N), serial VERIFY, bios **F2**` | maat.hardware `WeiBu ADL-N, CPU N100 (Frank was right)`; maat.bios = the AC-restore setting note | `WeiBu ADL-N, sn WBGMN9E0242800020, BIOS **5.27** 2024-01-17` (A) -> **DRIFT(inv)**: `bios: F2` is wrong. F2 is the AMI version on the two Seneca/Gigabyte boards (seshat, thoth); it was pasted onto maat. Real value 5.27.
- CPU: `Intel N-series (Alder Lake-N), 4` | `N100` | `Intel N100 4c/4t` (A) -> DRIFT(inv) generic model string
- RAM: `14 GB, ceiling VERIFY` | - | `1x16 GB DDR4-3200 Crucial CT16G4SFRA32A.M16FR, single slot, 15185 MiB` (A) -> **DRIFT(inv)** 14 -> 16
- Storage: `[nvme]` | - | `Micron 2200S 512GB NVMe sn 2020296E8024` (A) -> GAP
- NIC: `[]` | - | `enp2s0 00:e0:4c:0a:16:b5` (A) -> GAP
- Kernel/uptime: - | `7.0.0-27` | `7.0.0-30, up 6 days` (A) -> **DRIFT(state)**; uptime lines up with the 09-03 outage in inv known_issues.
- Board quirk: inv `Restore-on-AC does NOT persist` vs state `Restore-on-AC = Power On set by Frank 07-17` -> compatible (set, but does not hold). MATCH.

### seshat (Seneca PRO925430) - probed
- make/model/serial/BIOS: `Seneca PRO925430, serial VERIFY, bios VERIFY` | seshat.board `MTGU3CS-SD (Seneca PRO925430), AMI F2 2021-07-29` | `Seneca PRO925430, sn 2420062, board GIGABYTE MTGU3CS-SD sn LK7U6101947, BIOS F2 2021-07-29` (A) -> MATCH; GAP(inv serial/bios)
- CPU: `i3-1115G4, 2 cores` | same | `i3-1115G4 2c/4t, UHD G4` (A) -> MATCH
- RAM: `16 GB` | `16GB` | `1x16 GB DDR4-2400 Micron 16ATF2G64HZ-2G3E1 in Controller1 slot, Controller0 slot EMPTY, 15198 MiB` (A) -> MATCH (note: single stick, one slot free)
- Storage: inv `[NVMe 1TB SK hynix PC611]` | seshat.hardware 09-08 `boot 128GB Samsung MZMTE128 mSATA; 1TB SK hynix S31 SATA for /data (pending Frank swap 09-08 eve); old 1TB PC611 NVMe being removed`; storage_layout 09-08 `PC611 still installed, unmounted` | **only `sda SAMSUNG MZMTE128 119.2G sn S1F0NSAG220353`**. The PC611 is gone. The S31 is NOT present. Root is `/dev/sda2` 116G with 15G used. There is no `/data` directory or mount at all (A) -> **DRIFT(inv)**: lists a drive that has left the box and omits the one it boots from. **DRIFT(state)**: storage_layout still says the PC611 is installed. The box rebooted ~09-09 (uptime 1d3h, kernel 7.0.0-31) and the S31 did not appear, consistent with the SATA-port PHY-offline diagnosis already recorded on **wq#1341 (frank_gated, physical: BIOS SATA Port 1 enable)**. No new ticket: that one is the fix.
- Network: inv `192.168.8.219` | same | `enp175s0 98:b7:85:24:35:74 has static .219 AND a secondary dynamic .237 (dhcp lease)`; two more NICs `enp174s0 d8:5e:d3:81:57:7b`, `eno1 d8:5e:d3:81:57:7a` unused (A) -> **DRIFT(live)**: a dhcp4 client is still running alongside the static address. Ops-debt, ticketed below.
- Kernel: - | `7.0.0-27` | `7.0.0-31` (A) -> **DRIFT(state)**

### sia (N100 mini-PC, make unknown) - probed
- make/model/serial/BIOS: `VERIFY` | - | **not readable**: `sudo -n` on sia asks for a password, so dmidecode is blocked (A). The other 12 probed nodes have NOPASSWD for the fleet key -> sia is the odd one out. Ops-debt, ticketed below.
- CPU: `Intel N100, 4` | - | N100 4c/4t (A) -> MATCH
- RAM: `14 GB` | - | `15298 MiB` (A, DIMM detail blocked) -> **DRIFT(inv)** 14 -> 16 (same rounding class as bes/sobek/maat)
- Storage: `[nvme]` | - | `SK hynix BC711 HFM001TD3JX013N 953.9G sn FSC5N519012105S5F` (A) -> GAP(inv); it is the only N100 with a 1TB drive.
- NIC: `[{iface: VERIFY, mac: VERIFY, note: 2.5G RTL8125}]` | sia.nic_enp1s0 (07-10) `RTL8125 PCIe 01:00.0, MAC pinned 02:76:c6:00:8d:e6 in netplan (LAA)` | `enp1s0 02:76:c6:00:8d:e6` + onboard `enp2s0 9c:6b:00:20:16:62` (A) -> **DRIFT(inv)**: both VERIFYs were answerable from state.
- Kernel: - | - | `7.0.0-30` (A)

### sobek (Trigkey Green G4) - probed
- make/model/serial/BIOS: `VERIFY` | sobek.provisioned `Trigkey Green G4 N100 16GB/512GB NVMe` | `Trigkey Green G4, sn BN1003LH12393, BIOS ADLNV104 2023-07-17` (A) -> GAP(inv), answerable from state
- RAM: `14 GB` | `16GB` | `1x16 GB DDR4-3200, second slot empty, 15286 MiB` (A) -> **DRIFT(inv)** 14 -> 16
- Storage: `[nvme]` | `512GB NVMe` | `generic "PCIe SSD" 476.9G sn BF1907380A4800707715` (A) -> GAP(inv)
- NIC: `[]` | - | `enp1s0 7c:83:34:bc:f1:c1, wlo1 74:3a:f4:41:2b:de` (A) -> GAP
- Plug: inv `plug_id VERIFY` | sobek.provisioned: `Power: UNMETERED (not on any Tapo despite p5 socket assumption)`; p5.powers_node = `UNTRACED-78W-load` | - -> **DRIFT(inv)**: plug_id should be `none` with the unmetered note. (Separately: p5 has a 78W load nobody has traced; isis.power_plug says P5. Both records exist in state; not resolved here.)
- Kernel: - | - | `7.0.0-30` (A)

### thor (Samsung Galaxy Book Pro 360) - probed
- make/model/serial/BIOS: `Galaxy Book Pro 360, serial VERIFY` | thor.hardware `Galaxy Book Pro 360 (Xubuntu 26.04)` | `SAMSUNG 930QDB / NP930QDB-KE4US, sn 5BSC9FFT200313H, BIOS P15AKH.030.240429.KS 2024-04-29` (A) -> GAP(inv serial/bios)
- CPU/GPU: `i7-1165G7, 4; Iris Xe` | - | same 4c/8t (A) -> MATCH
- RAM: `15 GB LPDDR4x, ceiling VERIFY (soldered)` | - | `8x2 GB LPDDR4-4267 (UBE3D4AA-MGCR), 15661 MiB` (A) -> **DRIFT(inv)** 15 -> 16; ceiling 16 (soldered, no slots)
- Storage: inv `[NVMe 7.6G]` | - | `nvme0n1 SK hynix SHGP31-500GM-2 (Gold P31 500GB) 465.8G sn CD13T000410203H0F`; **`zram0 7.6G` is the swap device** (A) -> **DRIFT(inv)**: wq#1580's probe recorded the zram size as the NVMe. Probe bug, ticketed below.
- Network: `192.168.8.132` | same | `wlp45s0 1c:c1:0c:e1:ea:6f, dynamic lease` + libvirt `virbr0 192.168.122.1` (A) -> MATCH, note it is DHCP not static (no reservation recorded anywhere).
- Kernel: - | - | `7.0.0-31`, up 4d17h (A)

### thoth (Seneca PRO986222) - probed
- make/model/serial/BIOS: `Seneca MP mini PC, board "Seneca MP (same platform as seshat)"` | thoth.board `MTGU3CS-SD (Seneca), AMI F2, same board as seshat`; thoth.hardware `Seneca MP` | `Seneca **PRO986222**, sn 2581111, board GIGABYTE MTGU3CS-SD sn MI1N8201244, BIOS F2 2021-07-29` (A) -> MATCH on board; product code differs from seshat (PRO925430) - same board, different SKU. GAP(inv serial).
- CPU: `i3-1115G4, 2` | same | same (A) -> MATCH
- RAM: `14 GB` | - | `2x8 GB DDR4-3200 Samsung M471A1K43EB1-CWE, both slots used, 15198 MiB` (A) -> **DRIFT(inv)** 14 -> 16; and unlike seshat, no free slot.
- Storage: inv `[sata]` | thoth.storage_layout (09-01) `sda 256GB MZMTE256 mSATA = boot; sdb 4TB WD WDS400T2B0A = /mnt/storage` | `sda MZMTE256 238.5G sn S1F1NYAF637153; sdb WDS400T2B0A 3.6T sn 211146800508, /mnt/storage = sdb2 ext4` (A) -> GAP(inv), state correct
- Network: `192.168.8.218` | same | `enp174s0 98:b7:85:24:35:43` + `wg0 10.5.0.2/16` (WireGuard, matches thoth.wg_endpoint) + unused `enp173s0`, `eno1` (A) -> MATCH; inv nics empty
- Status: inv known_issue `tailnet-OFFLINE since ~09-02 ... Gatus alert must keep firing` | thoth.status (09-02) `tailnet-OFFLINE ... no remote shell` | **online, ssh works with the per-node key, up 1 week (rebooted ~09-03)** (A) -> **DRIFT(inv + state)**: both carry a 09-02 outage as present tense. thoth.ssh_status (07-10, "TS-SSH check-mode") is also still current-slice although thoth.tailscale_ssh (07-16) superseded it.
- Kernel: - | - | `7.0.0-30` (A)

### wadjet (Mac Pro 6,1 per prose) - OFFLINE, not probed
- Hardware: inv **all VERIFY** | state has **zero** hardware facts for wadjet (14 facts, all role/HA/kernel/IP) | timeout -> **GAP(both)**. The only description is prose (MAC_POWER_DEPLOYMENT / memory: Mac Pro 6,1, GPU-B absent, RAPL 75->45W). Grade D. Record from prose flagged unverified; verify on next power-on.
- Power: inv `off` | availability (09-10) `OFFLINE 53d` | tailnet last seen 07-19 (B) -> MATCH

### nephthys (Mac Pro 6,1) - decommissioned, not probed
- CPU: inv `Xeon E5 v2, cores VERIFY` | nephthys.os (06-28, live) `E5-2697 v2 24t`; nephthys.availability (Frank 09-08) `Xeon 2699 v2` | - -> **CONFLICT(state)**: the 06-28 read says 2697 v2 (12c/24t), Frank's 09-08 statement says 2699 v2 (12c/24t, higher clock). Same core count, different SKU. Per the FRANK'S-STATEMENT rule the observation needs explaining, not overriding: recorded in the YAML as `E5-2697 v2 per lscpu 2026-06-28 (Frank 2026-09-08: 2699 v2) - confirm at next power-on`.
- RAM: inv `size VERIFY, DDR3-ECC` | nephthys.ram (07-15) `64GB 4x16 Samsung M393B2G70QH0-CMA DDR3-1866` (C) -> **DRIFT(inv)**: answerable from state.
- Storage: inv `NVMe (proprietary)` | - | isis/hathor (same model) show the Apple blade as `APPLE SSD SM0256 ... sata` (AHCI), so "NVMe" is a mislabel for this generation -> DRIFT(inv wording).
- NIC: inv `[]` | nephthys.nic `enp12s0 00:3e:e1:cc:4b:17` (C) -> GAP(inv)
- BootROM: - | `481.0.0.0.0` (C) -> GAP(inv)

### 3dprinter (LG 24CQ650I-6N AIO) - no ssh path
- inv `active / power on / monitor true (alert off)` | 3dprinter.hardware LG AIO + 9 klipper/ender/prusa facts, tailscale_ip; no lan_ip, no ssh, no OS fact | tailnet **OFFLINE since 2026-07-26** (B) -> **DRIFT(inv)** `power: on` has been false for 46 days. Hardware beyond the model name is unrecorded everywhere (grade D).

### be3600 (GL.iNet Slate 7) - no ssh path
- inv `active / on; lan_ip VERIFY (comment: gateway of 192.168.0.x); services "tailscale (subnet/exit node - VERIFY)"` | role + tailscale_ip only | tailnet **OFFLINE since 2026-06-17** (85 d); apep's default gateway on the workbench LAN is `192.168.0.1` (ARP a8:6e:84:ad:0f:14, grade B) (B) -> The router is routing (apep reaches the tailnet through it) but its own Tailscale node has been dead for 85 days, so the "subnet/exit node" claim is unverified and probably not functioning. lan_ip 192.168.0.1 (B).

### toilet (unknown laptop, Frederick MD) - no ssh path
- inv all VERIFY, `location: VERIFY` | toilet.location `Frederick MD` (since 2026-01-01, never re-verified), status online 06-17 | tailnet hostname `toilet-940X5N`, linux, online, last seen 09-09 (B) -> GAP(inv location: state has it). The hostname's `940X5N` suffix looks like a Samsung Galaxy Book model code - unverified, recorded as a hint only. No owner, purpose, or ssh path anywhere.

### phone (Galaxy S26 Ultra)
- inv `lan_ip VERIFY, nics []` | state subject **s26-ultra** (not phone): `lan-ip 192.168.8.139 (DHCP reservation, wifi MAC b2:51:bb:af:85:33)` | tailnet online android (B) -> GAP(inv): both values exist in state under a different subject name and a hyphenated predicate.

### apis, aten, hapi-1, mirror, pbox
- **apis**: dead 08-13; tailnet last seen 08-13 (B) - consistent. Hardware never recorded in any store (only the SATA migration note). MATCH/GAP.
- **aten**: inv hardware was seeded from aten.hardware (i5-8250U, 8GB, PM981) - MATCH. Not on the tailnet; provisioning since 07-21 (51 days).
- **hapi-1**: nothing verifiable; chassis-level facts live under subject `hapi` (11 facts), blade-level under `hapi-1` (3 facts). MATCH.
- **mirror**: tailnet offline since 04-03; state 1 fact. MATCH.
- **pbox**: inv `power: off`; tailnet **last seen 2026-09-10** (B) -> **DRIFT(inv)**: the box is powered and on the tailnet (Frank's gaming box), it is just not fleet. `power: on` + decommissioned is the true state.

## 3. The VERIFY placeholders: what is actually knowable

wq#1580 filled 13/25 files from live ssh and left the rest. Counting after this pass:

| node | reachable now | VERIFYs resolvable from **live** | from **state** (grade C) | still unknown after both |
|---|---|---|---|---|
| lumen, anubis, bes, gameboy, hathor, isis, maat, seshat, sobek, thor, thoth | yes | serial, BIOS, DIMM layout, drive model+serial, NIC MAC, OS/kernel | - | psu_w (needs a label read), ups, ac_restore (BIOS screen), provisioned dates |
| apep | yes | everything except DMI (no dmidecode installed) | apep.hardware | serial, BIOS date is in state already |
| sia | yes | everything except DMI (sudo needs a password) | nic MAC | make/model/serial/BIOS until NOPASSWD lands |
| khepri | no (off since 08-15) | - | cpu, ram (4x16 Hynix), 256GB SM0256G, NIC MAC, OS | serial, BIOS |
| nephthys | no (decommissioned) | - | ram, NIC, BootROM 481, product | serial, storage blade model |
| wadjet | no (off since 07-19) | - | nothing structured | everything except IPs; prose says Mac Pro 6,1 |
| 3dprinter | no (off since 07-26) | - | model only | lan_ip, ssh user, cpu/ram/storage, plug |
| be3600 | Tailscale node dead 85d | - | role | lan_ip = 192.168.0.1 (grade B via apep); ssh user, firmware |
| toilet | online, no ssh path | - | location Frederick MD | identity, owner, purpose |
| phone | online (android) | - | lan_ip .139 + wifi MAC (subject s26-ultra) | serial |
| apis, aten, hapi-1, mirror, pbox | no | - | aten: full; hapi: chassis-level only | apis/mirror/pbox hardware were never recorded anywhere |

Net: 11 nodes can be made VERIFY-free for hardware today; 4 more (khepri, nephthys, sia-NIC, phone) can be
filled from state; wadjet, 3dprinter, be3600, toilet, apis, mirror, pbox need a hands-on read or stay
honestly VERIFY.

## 4. Every contradiction found, with the authoritative source

Rule applied: a fresher **read** beats an older read; a Frank **statement** beats any read (the read is
what needs explaining); prose never beats a structured fact.

| # | node.field | inventory | state | live | authoritative | why |
|---|---|---|---|---|---|---|
| 1 | lumen.ram | 60 | 64 (4x16) | 4x16 DDR4-2667 | live/state | inv is `free -g` rounding |
| 2 | lumen.storage | 1 sata drive | 9 drives incl. Patriot sda, md1 inactive, 970 PRO at nvme4 | 9 drives; sda is a Seagate 2TB; md1 active; 970 PRO at nvme5 | live | state is a 07-17 snapshot; /dev/nvmeN names are not stable across boots on this box |
| 3 | lumen.gpu | none | Lexa PRO 2GB, video-only directive | Lexa PRO 0d:00.0 | state+live | inv simply empty |
| 4 | lumen.nics | enp0s25 only | ens1 primary + enp0s25 dormant "enumerating" | ens1 only; enp0s25 has no netdev | live (+ Frank's never-spoof rule kept) | inv lists the dormant NIC and omits the active one; state's "enumerating" is stale |
| 5 | lumen.lan_ip | 192.168.8.160 | no atomic fact (inside nic_config prose) | 192.168.8.160 | inv/live | GAP in state -> ingested this session |
| 6 | anubis/isis/maat/seshat/apep .kernel | - | 7.0.0-27 / -27 / -27 / -27 / 6.8.0-134 | -30 / -30 / -30 / -31 / -138 | live | state kernel facts are one-shot observations that nothing refreshes |
| 7 | anubis/bes/hathor/isis/maat/sia/sobek/thor/thoth .ram | 30/14/60/60/14/14/14/15/14 | (16 where recorded) | 32/16/64/64/16/16/16/16/16 | live | the whole class = MemTotal rounding written as module size |
| 8 | maat.board.bios | F2 | (settings note) | 5.27 | live | F2 was copied from the Seneca boards |
| 9 | seshat.storage | PC611 1TB NVMe | S31 pending, PC611 being removed / still installed | only the 128G mSATA | live | drive left the box 09-09; S31 not detected (wq#1341) |
| 10 | seshat.network | .219 | .219 | .219 + dhcp .237 | live | undeclared dhcp4 still running |
| 11 | thor.storage | NVMe 7.6G | - | Gold P31 500G; 7.6G is zram | live | wq#1580 probe took the first block device |
| 12 | thoth.status | offline since 09-02 (known_issue) | offline since 09-02 | online, up 1 week | live | both stores froze the outage; nothing cleared it when the box came back |
| 13 | thoth.ssh_status vs thoth.tailscale_ssh | - | 07-10 check-mode AND 07-16 RunSSH=false both current | key ssh works | state (07-16) | older predicate never invalidated (different predicate name, so no supersession) |
| 14 | khepri.hardware | VERIFY x9 | full (08-14 live) | - | state | inv never fell back to the store |
| 15 | khepri.power | VERIFY | OFFLINE 26d | offline since 08-15 | tailnet | |
| 16 | nephthys.cpu | Xeon E5 v2 | 2697 v2 (06-28 read) vs 2699 v2 (Frank 09-08) | - | Frank's statement, read flagged | same 12c/24t either way; confirm SKU at power-on |
| 17 | nephthys.ram/nic | VERIFY | 64GB Samsung kit; enp12s0 MAC | - | state | inv never fell back to the store |
| 18 | sobek.plug_id | VERIFY | UNMETERED / none | - | state | |
| 19 | sia.nics | VERIFY iface/mac | enp1s0 02:76:c6:00:8d:e6 | same | state=live | |
| 20 | hathor.power_plug (text) | P3 | "P3 ... Hathor remains powered off/parked" | up 9 days | hathor.status 09-08 | stale clause inside a current fact |
| 21 | 3dprinter.power | on | - | tailnet offline 46 d | tailnet | |
| 22 | be3600 tailscale role | "subnet/exit node - VERIFY", active | - | tailnet node dead 85 d | tailnet | router works (apep routes via 192.168.0.1) but its TS node does not |
| 23 | pbox.power | off | decommissioned | tailnet seen today | tailnet | on, just not fleet |
| 24 | phone.lan_ip | VERIFY | 192.168.8.139 under subject s26-ultra / lan-ip | - | state | subject + predicate naming split |
| 25 | toilet.location | VERIFY | Frederick MD | - | state | |
| 26 | gameboy.lan_ip | .190 | `192.168.8.190_static_nmcli` | .190 | inv/live | value not atomic -> drift tool reports NO-FACT |
| 27 | isis.gpu | "VERIFY (runs Qwen3-32B)" | dual D300 dpm=low; model runs on CPU | dual Curacao XT | state+live | inv wording implies GPU inference |
| 28 | wadjet.hardware | VERIFY x9 | none | - | nobody (grade D prose) | genuine hole in every structured store |
| 29 | drift tool verdict | "0 disagreements" (fd5404b) | | | this report | `fleet-inv drift` compares 3 network fields only; 28 real disagreements sit in fields it does not look at |

## 5. Source-of-truth recommendation and reconciliation plan

### 5.1 The class of defect
Every one of #1-#29 is the same thing: **an observation hand-copied into a store that nothing
refreshes**. Rounded RAM, a drive that was swapped, a kernel that was upgraded, an outage that ended,
a NIC that lost its driver. Neither store is wrong by design; both are wrong by decay, because both are
written by hand (or by a one-shot probe) and read forever. `fleet-inv drift` then reports 0 because it
compares the three fields that happen to be copied carefully.

The DESIGN_SOT premise (YAML = truth, state may cache-derive) is correct for **declared** facts and wrong
for **observed** ones, which is exactly the split wq#1631 is now building. This report is the evidence
base for that split; it does not fork the design.

### 5.2 Which store wins for which field

| field class | examples | owner | written by | refresh |
|---|---|---|---|---|
| **Declared** (a decision) | status.status, monitor, monitoring.*, roles, services, serves, governance.*, ops.maint_tier, ops.known_issues, power_hw.plug_id, power_hw.recovery, ssh.method, ssh.key, nics[].note (NEVER SPOOF) | inventory YAML | humans / orchestrator PR | on decision |
| **Identity** (stable, read once) | make_model, serial, board.model, board.bios (version), cpu.model/cores, ram dimms/part numbers/ceiling, storage model+**serial** (never /dev name), nics iface+mac, gpu | state_facts | a **probe** (dmidecode/lsblk/lscpu/ip link) via `lumen_state.py ingest`, source_ref = probe run | on provision + on demand (`fleet-inv verify <node>`) + when the drift check sees a delta |
| **Observed-volatile** | os, kernel, uptime, tailnet online/last-seen, ssh reachable, root_disk %, md array state, secondary IPs, power on/off | **neither** as a durable fact | live probe at read time (`fleet-inv check`, sentinel, drift-assert) | every read; if persisted at all, with `half_life_days` and a `last_confirmed_at` refresh so it goes DUE instead of lying |
| **Network identity** | tailscale_ip, lan_ip (static), ssh.user | state_facts (as #1631 says), projected into YAML with a generated-from header | probe + provisioning | tailscale status is the cheap cross-check |
| **Frank statements** | "P3 is Hathor's plug", "2699 v2", "video-output only" | state_facts with `source_ref=Frank <date>` | ingest at the moment he says it | never overridden by a read; a conflicting read gets recorded next to it and flagged |

Concretely for the YAML: `hardware.*` and `network.tailscale_ip/lan_ip/ssh.user` become generated
(header line `# generated-from: state_facts <ts> - do not hand-edit`), everything else stays hand-owned.
`ops.last_verified` becomes the timestamp of the last successful probe, written by the probe, not by hand.

### 5.3 Rules that fall out of the evidence
1. **Key drives by serial, never by /dev/nvmeN.** Lumen's boot drive moved nvme4 -> nvme5 between two
   reads (#2). lsblk `SERIAL` is stable; the name is not.
2. **RAM = sum of DMI type-17 module sizes, not MemTotal.** #7 is nine files wrong by the same 1-4 GB.
3. **One block device = one storage entry; filter `TYPE=disk` and drop loop/zram.** #11.
4. **A status fact must carry its own expiry.** thoth.status (#12) and the kernel facts (#6) are true
   statements about a moment, stored as if permanent. Either don't persist them, or persist with a
   half-life and let the DUE marker do its job (the state store already has the columns).
5. **A probe that cannot read a node falls back to the store, and says so.** wq#1580 left khepri and
   nephthys at VERIFY (#14, #17) with grade-C answers sitting one query away.
6. **Predicate hygiene**: `tailscale_ip`/`ts_ip`, `lan_ip`/`lan-ip`, `hardware`/`hw`, `gameboy`/`gameboy-pc`,
   `phone`/`s26-ultra`, `hapi`/`hapi-1`, and values with suffixes (`_static_nmcli`) all defeat the
   supersession index (unique open fact per subject+predicate). #13 is the mechanism: rename the
   predicate and the old "truth" lives forever.
7. **The drift check must cover the fields that decay.** Extend `fleet-inv drift` from 3 network fields to
   the identity class above, compared against a fresh probe, not against the store alone (#29).
8. **Schema gap**: add `software: {os, kernel}` (observed-volatile, generated) so "OS 26.04" stops living
   in known_issues, and add `identity.tailnet: {hostname, tags}` because 6 nodes are tagged and 19 are not
   and nothing records which.

### 5.4 Reconciliation plan (ordered)
1. **Now (this ticket)**: land the YAML corrections below (fleet-inventory PR) and ingest the live-read
   identity facts + the stale-fact corrections into state_facts (done this session, `source_ref=wq#1666
   live probe 2026-09-10`), then `state-regen`. This makes both stores true *today*.
2. **wq#1631 (in flight)**: split ownership per 5.2; generate hardware/network blocks from the store; delete
   the YAML-is-truth line; `drift` becomes schema + declared-vs-store check.
3. **New ticket A - probe writer**: turn `/tmp/work-1666-022257/collect.sh` (this session's probe) into
   `fleet-inv verify [<node>|--all]`: dmidecode/lsblk/lscpu/ip-link -> normalized identity facts ->
   `lumen_state.py ingest` (only on delta) -> `ops.last_verified`. Rules 1-3, 5 live here. Wires the
   fix for #7/#11 into the machinery instead of into this PR only.
4. **New ticket B - volatile facts get a half-life**: kernel/status/availability/root_disk/uptime
   predicates get `half_life_days` set at ingest (7-14 d) and the DUE renderer already in `current` output
   becomes the signal; drift-assert re-reads DUE facts. Fixes the #6/#12 class.
5. **New ticket C - predicate/subject hygiene pass**: merge the duplicate pairs in rule 6 (invalidate the
   loser, re-ingest under the canonical name), strip value suffixes, add a lint to `lumen_state.py ingest`
   that refuses a predicate within edit-distance 2 of an existing one for that subject.
6. **Physical (Frank, already queued)**: wq#1341 seshat BIOS SATA Port 1 (the S31). Nothing new.
7. **Ops-debt tickets** (filed this session): sia NOPASSWD sudo for the fleet key; seshat stray dhcp4 on
   enp175s0; lumen enp0s25 has no netdev (nic_config fact corrected; decide whether the I217-LM should stay
   unbound - it is the never-spoof NIC and currently cannot be spoofed because it does not exist).

### 5.5 YAML corrections landed with this ticket (fleet-inventory PR)
Hardware fills from grade-A reads: lumen (ram 64 + DIMM layout, 9-drive storage keyed by serial, gpu,
both NICs, serial FK6FV52, BIOS A34), anubis (OptiPlex 3090 UFF, 32GB, Kingston NVMe, serial, BIOS),
apep (eMMC added, kernel), bes/sobek (Trigkey Green G4, 16GB, drive serial, BIOS ADLNV104, serials),
gameboy (serial, UEFI, WiFi MAC), hathor/isis (MacPro6,1, 64GB, SM0256, dual D300, NICs, BootROM,
serials), maat (BIOS 5.27, N100, 16GB Crucial, Micron 2200S, NIC), seshat (mSATA only, S31 absent,
single DIMM + free slot, three NICs), sia (16GB, BC711 1TB, both NICs; make/serial stay VERIFY until
sudo), thor (Gold P31 500G, 16GB soldered, product NP930QDB-KE4US, WiFi DHCP), thoth (PRO986222,
2x8GB both slots, mSATA + 4TB WD by serial, NICs, wg0).
Fills from grade-C state: khepri (full hw block, power off), nephthys (ram, NIC, BootROM, cpu with the
Frank/observation note), sia nics, sobek plug none/unmetered, phone lan_ip + wifi MAC + alias s26-ultra,
toilet location.
Status corrections: thoth known_issue rewritten past-tense (recovered ~09-03, key ssh works); 3dprinter
power off + tailnet date; be3600 lan_ip 192.168.0.1 (B) + tailscale node dead since 06-17; pbox power on
(non-fleet); khepri power off; hathor/isis gpu wording; lumen "OS 26.04" moved out of known_issues into
a note pending the schema field; lumen ECC DIMM4 suspect carried as known_issue.
All `ops.last_verified` on probed nodes set to 2026-09-10.

### 5.6 What landed (2026-09-10)
- fleet-inventory PR #9 (73f4ffa): 23 machine files + `software:` schema key. PR #10 (ab61f88): 3dprinter
  declared `standby`, be3600 dead tailnet client acknowledged — both surfaced by wq#1631's new lifecycle
  check the moment the OFFLINE facts below were ingested, which is the check working as designed.
- state_facts: 64 facts ingested under `source_ref="wq#1666 live probe 2026-09-10"`, then `state-regen`.
  Predicate names used, so ticket A can generate from them without inventing new ones:
  `hardware` (make/model/serial/BIOS/CPU one-liner), `ram`, `storage` (or the existing `storage_layout`
  on seshat/thoth), `nic`, `kernel` (includes os + uptime), `status`/`power`/`lan_ip` where stale.
  Corrections: lumen.storage, lumen.nic_config, lumen.lan_ip (new), thoth.status, thoth.ssh_status,
  hathor.power_plug (stale clause), sobek.power_plug (none/unmetered), seshat.storage_layout, kernel x5.
- `fleet-inv drift` after landing: 0 (was 0 before, for the wrong reason — see #29).
