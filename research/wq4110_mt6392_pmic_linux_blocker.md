# wq#4110 — MIKO LINUX: MT6392 PMIC driver as critical-path blocker

**Date:** 2026-10-17 · **Author:** fworker-wq4110 · **Type:** research / go-no-go input
**Scope:** Can we bring up mainline (or near-mainline) Linux on the Miko 3 robot
(MediaTek **MT8167** SoC + **MT6392** companion PMIC)? Is the MT6392 PMIC driver really
the blocker, and what are the options — out-of-tree driver, BSP port, or ship without it?

## TL;DR

- **MT6392 has no mainline Linux driver.** MediaTek's MT8167 Linux enablement was partially
  upstreamed, but the PMIC support — "most importantly MT6392 PMIC" — was **left unmerged**
  (Nura wiki, MediaTek MT8167 page). The SoC-side plumbing the PMIC rides on (the MediaTek
  **PMIC wrapper / `pwrap`**, clocks, pinctrl, UART, MMC) is in mainline; the PMIC *client*
  stack is not.
- **It is the critical blocker, but it is not a wall.** The MT6392 is architecturally a
  sibling of the already-mainlined **MT6397/MT6323** family (regmap-over-`pwrap`, bucks +
  LDOs + RTC + power-key). A driver exists in MediaTek's **BSP kernel** for the MT8167/MT8516
  and is portable; and for a first boot you can get to a usable userland **without** it by
  relying on the bootloader's PMIC init (see "What breaks without it").
- **Recommended path:** stage it. (1) Boot-first: bring Linux up on the Pumpkin-derived DTS
  with the PMIC regulators left at bootloader defaults (fixed/always-on), no `mt6392` node —
  proves kernel/console/storage without touching power. (2) Port the BSP MT6392 mfd+regulator
  as an **out-of-tree module** modelled on `mt6397`. (3) Only then enable suspend/DVFS/thermal
  that actually need live regulator control. This de-risks the blocker instead of gating all
  of Linux bring-up on it.

## What the MT6392 is and why Linux needs a driver for it

The MT8167 is a fanless quad-A35 applications SoC; like every MediaTek application SoC it
does **not** regulate its own rails. Core voltage (Vproc), SoC/DRAM rails, and the LDOs that
feed MMC/IO/analog blocks all live on a separate **PMIC chip, the MT6392**, reached over
MediaTek's dedicated serial channel, the **PMIC wrapper (`pwrap`)** — not a normal I²C/SPI
bus. So a Linux driver for the PMIC is really three mainline layers:

1. **MFD / regmap parent** (`drivers/mfd/mt6392-*.c` in BSP) — brings up the regmap over
   `pwrap` and registers the sub-devices. Analogue of mainline `mfd/mt6397-core.c`.
2. **Regulator** (`drivers/regulator/mt6392-regulator.c`) — the bucks (Vproc, Vcore, Vdram…)
   and LDOs. Analogue of mainline `regulator/mt6323-regulator.c` / `mt6397-regulator.c`.
3. **Sub-functions** — RTC, power/reset key, and on some designs a simple charger/fuel-gauge.
   Mainline has `rtc-mt6397`, `mfd` power-key input for the sibling parts.

`pwrap` for MT8167 is already mainline (`drivers/soc/mediatek/mtk-pmic-wrap.c` supports the
mt8167 variant), so the transport is solved. The gap is purely the **MT6392 client stack**.

## Prior-art search (doctrine step 1)

`fleet-search web` (SearXNG/Google-CSE; most engines were rate-limited this run, see
ISSUE-LOG) + knowledge grounding:

- **Nura wiki — MediaTek MT8167** (`wiki.nura.eco/wiki/MediaTek_MT8167`): confirms several
  community Linux ports exist, Android/Lenovo downstream sources are available, and
  **"Some work (most importantly MT6392 PMIC) was left unmerged."** This is the single most
  on-point external corroboration: someone already did the driver work; it just never landed
  mainline. (Page is behind an Anubis proof-of-work bot wall to `fleet-search read`; the
  finding comes from the indexed search snippet.)
- **No `mediatek,mt6392` compatible in mainline.** Mainline MediaTek PMIC bindings cover
  mt6323/mt6358/mt6359/mt6397 (and newer Genio-era mt6315/mt6360/mt6319), **not** mt6392.
- **GitHub API was unauthenticated this run** (no usable token — see ISSUE-LOG), so the exact
  out-of-tree repo/commit could not be pinned from here. The driver is known to live in
  MediaTek's **MT8167/MT8516 BSP (Linux 4.4/4.9/4.14-class) kernels** and in vendor trees
  such as the AOSP/Lenovo MT8167 sources the wiki references. **Physical/Frank follow-up:**
  a GitHub token would let a follow-up ticket locate and link the exact BSP driver files.

## Options, ranked

### Option A — Port the BSP driver as an out-of-tree module (recommended for PM)
MediaTek's BSP already contains a working mt6392 mfd + regulator + rtc + power-key stack.
Porting it forward to a modern kernel is tractable **because a near-identical sibling is
already upstream** (`mt6397`/`mt6323`): the register map, buck/LDO descriptor tables, and the
regmap-over-`pwrap` pattern all have a mainline template to diff against. Work = forward-port
API churn (regulator/mfd/of APIs moved since 4.x), author a DT binding
(`mediatek,mt6392` modelled on `mediatek,mt6397`), and wire the `mt6392` node under the
`pwrap` node in the Miko DTS. **Effort: medium.** Lands real suspend/DVFS/thermal.

### Option B — Boot-first without the PMIC driver (recommended for *first* bring-up)
The bootloader (preloader/LK) already programs the PMIC to sane defaults before handing off
to Linux. If every rail Linux touches is left at those defaults — modelled as
`regulator-fixed`/`regulator-always-on` with **no** `mt6392` node — Linux boots, gets a
console, mounts eMMC/SD, and runs userland at the stock operating point. This is the standard
MediaTek "bring-up before PMIC" posture and is the fastest proof-of-life. **Effort: low.**
**Limitation:** no dynamic voltage control → see next section.

### Option C — Ship on the BSP kernel as-is (fallback)
Run Miko's vendor 4.x BSP kernel, which has the full MT6392 stack natively. Fastest to "full
power management working," but it's an abandoned vendor kernel (security, no mainline drivers,
hard to maintain). Use only if A proves too costly and full PM is required day one.

### Option D — Write a mainline driver from scratch / upstream it
Highest effort, only worth it if upstreaming is a goal. Not recommended for a device
bring-up; it's a superset of Option A's work plus the mainline review cycle.

## What breaks if you run WITHOUT the MT6392 driver (Option B reality check)

With the PMIC left at bootloader defaults and no Linux driver:

| Capability | Status without driver | Why |
|---|---|---|
| **Boot / console / userland** | ✅ works | Rails stay at bootloader defaults; CPU runs at stock OPP |
| **eMMC/SD, UART, USB, basic IO** | ✅ works (mostly) | Their supplies are on and static; model as fixed regulators |
| **Suspend-to-RAM / deep idle** | ❌ broken | Suspend must lower/gate PMIC rails and restore them; no regulator control = no safe suspend. Expect no real low-power state (idle only) |
| **CPU DVFS / `cpufreq`** | ❌ no voltage scaling | Vproc can't be moved, so frequency scaling is unsafe/absent → runs pinned at the bootloader OPP, higher power + heat |
| **Thermal throttling** | ⚠️ degraded | Thermal zones can still read temp and can gate clocks, but the strongest lever (drop Vproc/freq via DVFS) is gone; sustained load risks hitting trip points with only coarse mitigation |
| **Battery charging / fuel gauge** | ❌ none in-kernel | If Miko 3 is battery-backed, charger/gauge live on/with the PMIC; without the driver there's no charge control or capacity reporting from Linux (bootloader/HW may still trickle-charge) |
| **RTC (PMIC RTC)** | ❌ unavailable | MT6392 RTC needs the mfd child; use an alternate time source until ported |
| **Power/reset key events** | ❌ not delivered | Power-key is a PMIC mfd child; no clean shutdown-on-keypress |
| **Clean power-off / reset** | ⚠️ depends | Software poweroff often routes through the PMIC; may need a watchdog/SoC reset fallback |

**Bottom line:** without the driver you get a *running* system but a **power-dumb** one — no
suspend, no DVFS, weak thermal headroom, no battery management, no PMIC RTC/power-key. Fine
for a bench bring-up; **not** shippable for a battery robot that must sleep and manage heat.

## Recommendation

1. **Now:** Option B. Boot Linux on the Pumpkin-derived MT8167 DTS with PMIC rails modelled
   as fixed/always-on, no `mt6392` node. Proves kernel + console + storage. (A probe that
   can fail: boot, then confirm **no** `mt6392`/`mt6397` regulator registers in `dmesg`/
   `/sys/class/regulator` yet userland is up — and that `cpufreq` exposes only the single
   bootloader OPP. If a PMIC regulator shows up, the DTS wasn't actually PMIC-less.)
2. **Then:** Option A. Port the BSP MT6392 mfd+regulator+rtc as an out-of-tree module against
   the `mt6397` template; add the `mediatek,mt6392` DT node under `pwrap`. Gate suspend/DVFS/
   thermal-DVFS enablement on this landing.
3. **Fallback:** Option C (BSP kernel) only if full PM is required before A is ready.

**Follow-up tickets this implies**
- *(physical/Frank)* Provide a GitHub token to the fleet so a BUILD ticket can locate and
  link the exact MT6392 BSP driver files (mfd/regulator/rtc) and the Lenovo/AOSP MT8167 tree.
- *(BUILD)* Obtain the Miko 3 / Pumpkin MT8167 DTS and enumerate every supply Linux touches,
  to build the Option-B fixed-regulator overlay.
- *(BUILD)* Port MT6392 mfd+regulator out-of-tree (Option A), with a DT binding and a boot
  test that exercises at least one non-always-on rail (acceptance probe that can fail).

## Sources
- Nura wiki — MediaTek MT8167 (MT6392 PMIC "left unmerged"): https://wiki.nura.eco/wiki/MediaTek_MT8167
- Mainline MediaTek PMIC family (mt6397/mt6323 mfd+regulator+rtc, the port template):
  `drivers/mfd/mt6397-core.c`, `drivers/regulator/mt6323-regulator.c`, `drivers/rtc/rtc-mt6397.c`
- MediaTek PMIC wrapper (mt8167 transport, already mainline): `drivers/soc/mediatek/mtk-pmic-wrap.c`
