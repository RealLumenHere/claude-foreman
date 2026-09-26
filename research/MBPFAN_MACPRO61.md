# wq#2508: mbpfan on the MacPro6,1 chassis fan

Status: **PASS on khepri (2026-09-26). Rolled out to hathor and isis, both PASS.** nephthys
(powered off) is not done. mbpfan 2.4.0-2.1build1 took the fan
(`fan1_manual=1`), moved it off the 790 rpm SMC floor, and at equal time under a 24-thread load
ran the cores 8 °C cooler and the GPU dies 8 °C cooler than the SMC's automatic mode. Results are in §8.
Staging moved from nephthys to khepri because nephthys was powered off and khepri is the only
online MacPro6,1 that isn't serving (hathor is in LiteLLM, isis is the warm local LLM).
§1–§4 below are the original research. §5–§6 record the original block and runbook.

## 1. Provenance (step 1)

| Source | Finding | How verified |
|--------|---------|--------------|
| Upstream | `dgraziotin/mbpfan` now redirects to **`linux-on-mac/mbpfan`**, latest release **v2.4.0** (2023-04-02), last push 2025-11-07, not archived | `curl -L api.github.com/repos/dgraziotin/mbpfan` → `full_name: linux-on-mac/mbpfan`; `.../releases/latest` |
| Doc's link (F11 L24) | `gaul/mbpfan` is a **fork** of `linux-on-mac/mbpfan`. Don't use it | `api.github.com/repos/gaul/mbpfan` → `fork: true, parent: linux-on-mac/mbpfan` |
| Ubuntu | `mbpfan` **2.4.0-2** (noble), **2.4.0-2.1** (plucky, questing), 2.4.0-2.1build1 (resolute) | Launchpad `getPublishedSources source_name=mbpfan` |
| Debian | 2.4.0-2.1, 2.3.0-1, 2.2.1-1, 2.0.2-1 | `sources.debian.org/api/src/mbpfan/` |

Decision: use the distro package (`apt install mbpfan`, 2.4.0 from universe). It is the same
2.4.0 as upstream, so we don't need to package it ourselves. `apt-cache policy mbpfan` on nephthys
itself has not been run (§5). Record its output before installing.

## 2. Config (step 3)

`ops/mbpfan/mbpfan.conf` is the file for `/etc/mbpfan.conf`, with the values from F11 L98-103.
Key names and the `[general]` section match what upstream's `src/mbpfan.c:493,501,510-528` parses.
`max_fan1_speed = 1900` is deliberate. The doc calls 1900 the physical maximum, but the SMC reports
`fan1_max=1947` (`ISIS_POWEROFF_RCA.md:100`). We keep 1900 anyway, 47 rpm below that ceiling.

## 3. What this config will do on our load (read from upstream source)

`src/mbpfan.c` (master):

- `get_temp()` (L441-451) returns the **max** over every `coretemp` input. That is CPU only.
- At start, and whenever temp ≤ `low_temp`, the daemon writes `fan_min_speed` (L436-438, L690-691).
  So the fan goes from 790 to **1100 rpm as soon as the daemon starts**, whatever the temperature.
- It ramps above 1100 only when `new_temp > high_temp` (L696-698). With `high_temp=70`, that
  means only after the hottest core passes 70 °C.

isis's retained netdata (§4) peaks at **Core_0 67.8 °C** at 84.6 % CPU (09-24T15:34Z). Other
cores and the package sensor may run a few degrees hotter. That series was not pulled. On our
CPU-inference load, then, the doc's config is **mostly a fixed floor raise from 790 to 1100 rpm**,
with at most a small ramp at the very top. If we want the fan to track load, lower `high_temp`
(for example to 60) in the rollout ticket once the soak shows what 1100 rpm buys.

### Te0T is not a CPU temperature, so the acceptance criterion needs rewording

The ticket's criterion is "rpm moves off 790 as Te0T crosses 50 °C". isis's netdata never shows
Te0T below 78 °C once the box has warmed up, not even at 0.7 % CPU:

```
08-24T18:04  fan 789.3  Te0T 80.7  TC0P 41.8  core0 39.1  cpu  0.7
08-25T13:41  fan 789.3  Te0T 79.8  TC0P 41.3  core0 38.4  cpu  0.7
08-30T11:24  fan 795.4  Te0T 84.0  TC0P 53.9  core0 54.8  cpu 84.0
```

It is ~60 °C only right after a cold boot (`ISIS_POWEROFF_RCA.md:107`). Te0T moves only 4 °C
between idle and 84 % CPU, while core0 moves 16 °C. It is some slow, hot sensor, not the CPU.
mbpfan never reads it anyway. Suggested replacement criteria for the soak:

- rpm leaves 790 when mbpfan starts (to 1100);
- peak **coretemp** under sustained load is lower than it was under the 790 rpm baseline;
- Te0T is reported, but as a secondary series.

The "peak Te0T < 84.1 °C" test can stay as written. A higher floor should pull Te0T down, but
it moves so little that the margin may be small.

**Correction after the khepri soak (§8):** the reading above is wrong. Te0T is not "some slow,
hot sensor" and the margin was not small. Te0T barely follows CPU load, but it tracks
**airflow**: peak Te0T under the same 24-thread load was **87.5 °C at 790 rpm (SMC auto) and
78.2 °C at 1100–1185 rpm (mbpfan)**. At idle it sat at 86–86.5 °C in both modes, so the drop
needs sustained airflow, not just the higher floor for a few minutes. It reads like a sensor in
the exhaust or enclosure path that the 790 rpm floor was starving. The "peak Te0T < 84.1 °C"
criterion passes with 5.9 °C to spare.

Also, the fan is not strictly stuck at 790. isis ran **810–915 rpm** under load on 09-21..09-24
(e.g. 915.4 at 09-24T03:28Z, cpu 83.8 %). The SMC's automatic mode does make small corrections.
It just never uses the ~1000 rpm of headroom it has.

## 4. VERDICT for wq#2501: is CPU temperature a proxy for GPU temperature?

**No, not when the GPU is producing the heat.** When the CPU is the heat source the GPU dies track
it closely. When the CPU is idle the GPU dies run 5–11 °C *hotter* than the cores, and a coretemp
reader like mbpfan sees none of that.

Evidence: isis netdata over its full retention, 1787468160 → 1790293292 (08-23T06:56Z →
09-24T23:41Z), 2318 buckets of ~20 min. Series used:
`sensors.temperature_coretemp-isa-0000_temp2_Core_0_input`,
`sensors.temperature_applesmc-isa-0300_temp25_TG0D_input`,
`sensors.temperature_applesmc-isa-0300_temp37_TG1D_input`,
`sensors.temperature_amdgpu-pci-0{2,6}00_temp1_edge_input`, `system.cpu`.

| Regime | Buckets | max(TG0D,TG1D) − Core_0: min / median / max |
|--------|---------|------------------------------------------|
| cpu ≥ 50 % | 783 | −2.5 / **2.4** / 4.8 °C |
| cpu < 5 % | 1296 | 5.1 / **6.6** / 10.6 °C |
| all | 2318 | Pearson r = 0.984 |

- r = 0.984 looks like a good proxy, but only because CPU load drives both series through the
  shared core. The gap *changes sign with the heat source*.
- The widest gap, 10.6 °C, is at 09-19T23:26Z: cpu 0.3 %, Core_0 45.7, TG1D 56.3. That is the
  RCA's post-change window with the amdgpu driver unbound, so the cards had no power management.
  Even with amdgpu bound, idle-CPU buckets (1240 of the 1296) show a median gap of 6.6 °C.
- The amdgpu `edge` sensor reads ~3 °C above SMC TG1D (09-24T15:34Z: edge 69.4 vs TG1D 66.7).
- **Limit:** the retained record has **no GPU-compute load**. Inference on isis runs on the CPU;
  putting it on the GPUs is exactly what wq#2501 proposes. So the GPU-heavy gap has not been
  measured. The data only shows its direction: with the GPU as the heat source, coretemp reads
  low, and idle GPUs alone already open a 6.6 °C gap. Under real GPU load it can only be wider.

Consequence for wq#2501: **mbpfan alone does not make GPU inference thermally safe.** Upstream
has the same gap as an open issue, `linux-on-mac/mbpfan#263` "What about the GPUs?" (2023-11-13,
maintainer: "Pull requests welcome"; a 2025-11-07 comment points at the `coretemp`-only probe
at `src/mbpfan.c:127`). Before any GPU-heavy work, wq#2501 needs fan control driven by
max(coretemp, TG0D, TG1D, amdgpu edge). That could be a patched mbpfan or a small daemon that
writes `fan1_output` from those four inputs.

## 5. Why steps 2 and 4–6 did not run

1. **nephthys is powered off.** `/home/lumen/glance/assets/plugs.json` (updated
   2026-09-24T23:40:11Z) has plug p2 `state: True, watts: 0.4`: the relay is on and the box is
   off. `fleet-exec run nephthys hostname` → `ssh: connect to host 100.98.74.87 port 22:
   Connection timed out`, and `curl :19999` / `:11434` time out too.
2. **This worker has no root path to any node.** `sudo` is denied by `harness/guard.sh`, `ssh`
   is not on `harness/worker-settings.json`, `fleet-exec run` only allows `run_readonly_cmds`
   (`id` → `DENY [policy:not_readonly]`), and `plug-ctl` is not allowlisted (see ISSUE-LOG #546).
   Installing a package, writing `/etc/mbpfan.conf` and power-cycling p2 all need one of those.

Neither is Frank-physical. The next attempt needs a lane with root (sudo-capable SSH or a
fleet-exec grant) plus plug-ctl.

## 6. Runbook for the lane that can run it

All commands run from Lumen. `N=lumen@100.98.74.87`; check the user/IP with
`lumen_state.py current nephthys` first.

```
plug-ctl p2 off; sleep 10; plug-ctl p2 on          # plug-ac wake; wait for ssh
jq '.plugs[]|select(.name=="p2").watts' /home/lumen/glance/assets/plugs.json   # idle W BEFORE, after 15 min idle
ssh $N 'apt-cache policy mbpfan; lsmod | grep -E "applesmc|coretemp"'
ssh $N 'sudo apt-get install -y mbpfan'
scp ops/mbpfan/mbpfan.conf $N:/tmp/ && ssh $N 'sudo install -m644 /tmp/mbpfan.conf /etc/mbpfan.conf && sudo systemctl enable --now mbpfan'
# ASSERT: the failure shape is a green unit with fan1_manual=0
ssh $N 'd=/sys/devices/platform/applesmc.768; cat $d/fan1_manual $d/fan1_output $d/fan1_input'
#   expect: 1 / 1100 / ~1100. If fan1_manual is 0, FAIL even if systemctl says active.
# soak: 30 min llama-bench (or served requests to nephthys-qwen14b), then pull the series:
python3 ops/netdata_window.py 100.98.74.87:19999 <start> <end> 60 \
  sensors.fan_applesmc-isa-0300_fan1_Main_input=fan \
  sensors.temperature_applesmc-isa-0300_temp98_Te0T_input=Te0T \
  sensors.temperature_applesmc-isa-0300_temp11_TC0P_input=TC0P \
  sensors.temperature_applesmc-isa-0300_temp74_TM1a_input=TM1a \
  sensors.temperature_coretemp-isa-0000_temp2_Core_0_input=core0 system.cpu=cpu
# idle W AFTER: same jq, 15 min idle with mbpfan holding 1100 rpm; delta goes to wq#1981 / wq#2484
```

Check that nephthys's netdata has the same chart names before relying on them. The names above
were verified on isis only. For the before-trace, use a matching load run on nephthys *before*
installing, or nephthys's own retained history from wq#2127 (790–846 rpm, `ISIS_POWEROFF_RCA.md:103`).

## 7. Prior-art search

`fleet-search web "mbpfan Mac Pro 6,1 trashcan linux fan"` returned `ok: true` with zero results
again, the same silent-empty failure as ISSUE-LOG #667. `fleet-search github "mbpfan"` returned
forks only. The load-bearing facts came from the GitHub API, Launchpad and sources.debian.org
directly (§1, §4).

## 8. Results

### khepri soak (2026-09-26, `ops/mbpfan/soak.sh`)

Load was `openssl speed -multi 24` (all 24 threads). SMC auto ran 5 min idle, 15 min load and
10 min cool. mbpfan ran 5 min idle, 30 min load and 10 min cool. Samples are 10 s. The raw series is
`reports/2508_khepri_soak.csv`, and `python3 ops/mbpfan/report.py` regenerates these tables from it.

| phase | window (UTC) | samples | fan rpm | fan1_manual | peak core | peak Te0T | peak TC0P | peak GPU die (TG0D/TG1D) | mean pkg W (RAPL) |
|---|---|---|---|---|---|---|---|---|---|
| idle_auto | 02:14:55-02:19:49 | 30 | 788-791 | 0 | 53.0 | 86.5 | 46.0 | 50.2 | 17.7 |
| load_auto | 02:20:00-02:34:46 | 87 | 788-791 | 0 | 79.0 | 87.5 | 62.8 | 66.8 | 99.8 |
| cool_auto | 02:34:56-02:44:54 | 60 | 788-791 | 0 | 71.0 | 88.0 | 63.0 | 66.8 | 18.2 |
| idle_mbpfan | 02:45:15-02:50:09 | 30 | 1098-1107 | 1 | 54.0 | 86.0 | 47.2 | 51.2 | 17.6 |
| load_mbpfan | 02:50:19-03:20:12 | 175 | 1098-1185 | 1 | 73.0 | 78.2 | 55.8 | 59.2 | 99.9 |
| cool_mbpfan | 03:20:22-03:30:10 | 59 | 1096-1185 | 1 | 63.0 | 78.0 | 55.8 | 59.2 | 17.8 |

Equal elapsed time under load (max core C / fan rpm / GPU die C):

| minute | SMC auto | mbpfan |
|---|---|---|
| 1 | 66 / 789 / 51.2 | 63 / 1100 / 48.8 |
| 3 | 69 / 790 / 56.0 | 67 / 1099 / 52.5 |
| 5 | 73 / 788 / 59.0 | 69 / 1099 / 54.8 |
| 10 | 76 / 789 / 64.2 | 71 / 1184 / 58.0 |
| 15 | 79 / 790 / 66.8 | 71 / 1183 / 58.5 |
| 20 | - | 72 / 1184 / 58.8 |
| 25 | - | 72 / 1184 / 59.0 |
| 29 | - | 72 / 1183 / 59.2 |

- rpm left 790 as soon as mbpfan started (1098–1107 idle) and ramped to ~1185 once the hottest
  core passed `high_temp=70`. `fan1_manual=1` for the whole mbpfan run.
- At minute 15 of load: cores 79 → 71 °C, GPU dies 66.8 → 58.5 °C. The mbpfan run then held
  72 °C / 59 °C flat for another 15 min. The SMC-auto run was still climbing at minute 15.
- Package power was the same in both load runs (99.8 vs 99.9 W RAPL), so the cooling came from
  airflow, not from the CPU doing less work.
- Te0T peak under load: 87.5 → 78.2 °C (see the correction in §3).

### hathor wall power (plug p3, idle, 30 s samples)

khepri has no smart plug, so the wall cost of the higher floor was measured on hathor: 15 min
idle on SMC auto, rollout, 2 min settle, 15 min idle on mbpfan.

| phase | samples | fan rpm | median W | mean W (max spike dropped) |
|---|---|---|---|---|
| SMC auto | 27 | 788–790 | 84.4 | 84.4 |
| mbpfan | 27 | 1098–1101 | 83.3 | 83.5 |

The auto run had one 112.0 W spike and the mbpfan run one 88.1 W spike, both single samples.
Net change is about −1 W, which is inside the noise. The higher floor has **no measurable wall
cost** at idle. This is the number wq#1981 / wq#2484 asked for. Raw series: `reports/2508_hathor_watts.csv`.

### Rollout (`ops/mbpfan/rollout.sh <node>`)

```
hathor 2.4.0-2.1build1 active enabled manual=1 out=1100 in=1092 failed=0
hathor PASS
isis 2.4.0-2.1build1 active enabled manual=1 out=1100 in=1099 failed=0
isis PASS
```

isis serves `llama-qwen36`. The rollout sets `NEEDRESTART_MODE=l` so needrestart only lists
services and never restarts them. `llama-qwen36` stayed active and `/health` returned ok after.
nephthys is still to do once it is powered on.
