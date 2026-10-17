# wq#4110 — MIKO LINUX: MT6392 PMIC driver as critical-path blocker

**Date:** 2026-10-17 · **Author:** fworker-wq4110 · **Type:** research / go-no-go input
**Amended:** 2026-10-17 by fworker-wq4129 (see "Amendment" below) — Option A changed
**Scope:** Can we bring up mainline (or near-mainline) Linux on the Miko 3 robot
(MediaTek **MT8167** SoC + **MT6392** companion PMIC)? Is the MT6392 PMIC driver really
the blocker, and what are the options — out-of-tree driver, BSP port, or ship without it?

## Amendment (wq#4129, 2026-10-17)

wq#4123's verify-pass CONTRADICTED this doc; wq#4129 re-researched the upstream question
from primary sources and amends it. Three changes:

| # | Original text | Status | Correction |
|---|---|---|---|
| 1 | "No `mediatek,mt6392` compatible in mainline" (old line 56) | **partly wrong** | A mainline *pinfunc header* exists (`include/dt-bindings/pinctrl/mediatek,mt6392-pinfunc.h`, shipped in Linux 7.2). The MFD/regulator **compatibles** are still unmerged — they are patch 1-3 of an in-flight series. The blanket phrasing is replaced below. |
| 2 | Option A = "port MediaTek's BSP driver from scratch" | **superseded** | An active upstream series, **`[PATCH v10 0/9] Add support for MT6392 PMIC`** (Luca Leonardo Scorcia, 2026-09-02), already does exactly this work against a modern kernel, with maintainer Reviewed-by tags. Tracking/testing it is the new Option A; the BSP hand-port is demoted to Option A2. |
| 3 | — | process | wq#4123 reported this file as landed twice on `main`. Only `de8ace3616` is on `main`; `cb6839600a` is a stale lane-branch duplicate. See "Provenance" at the end. |

The original error was not a reasoning failure but an unretried search: wq#4110's own
prior-art pass was rate-limited (it says so), and nobody re-ran it with the narrow query
("add support for MT6392 PMIC") that surfaces the series immediately.

## TL;DR

- **MT6392 has no *merged* mainline Linux driver — but it has an in-flight one.** MediaTek's
  MT8167 Linux enablement was partially upstreamed and the PMIC support was left unmerged
  (Nura wiki, MediaTek MT8167 page); mainline today carries only fragments, e.g. the
  `mediatek,mt6392-pinfunc.h` pinctrl header. The SoC-side plumbing the PMIC rides on (the
  MediaTek **PMIC wrapper / `pwrap`**, clocks, pinctrl, UART, MMC) is in mainline; the PMIC
  *client* stack is not. What **is** in flight is a complete, reviewed 9-patch series —
  mfd + regulator + pinctrl + keys + RTC + dtsi — at v10 as of 2026-09-02, waiting on
  subsystem Acks (see "Upstream status").
- **It is the critical blocker, but it is not a wall.** The MT6392 is architecturally a
  sibling of the already-mainlined **MT6397/MT6323** family (regmap-over-`pwrap`, bucks +
  LDOs + RTC + power-key). A driver exists in MediaTek's **BSP kernel** for the MT8167/MT8516
  and is portable; and for a first boot you can get to a usable userland **without** it by
  relying on the bootloader's PMIC init (see "What breaks without it").
- **Recommended path:** stage it. (1) Boot-first: bring Linux up on the Pumpkin-derived DTS
  with the PMIC regulators left at bootloader defaults (fixed/always-on), no `mt6392` node —
  proves kernel/console/storage without touching power. (2) **Apply and test Scorcia's v10
  series** on our kernel and DTS (not a from-scratch BSP port). (3) Only then enable
  suspend/DVFS/thermal that actually need live regulator control. This de-risks the blocker
  instead of gating all of Linux bring-up on it.

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
- **No *merged* `mediatek,mt6392` MFD/regulator compatible.** Mainline MediaTek PMIC
  bindings cover mt6323/mt6358/mt6359/mt6397 (and newer Genio-era mt6315/mt6360/mt6319);
  `mediatek,mt6392` + `mediatek,mt6392-rtc` + the regulator/keys bindings arrive only with
  the in-flight series (its patches 1-3). ~~No `mediatek,mt6392` compatible in mainline~~ as
  originally written was too broad: a `mediatek,mt6392` *pinfunc* header is already shipping.
  **Evidence (primary source, re-fetched 2026-10-17):** the Fedora
  `kernel-devel-7.2.10-100.fc43` package file listing contains exactly one `mt6392` entry,
  `mediatek,mt6392-pinfunc.h`, in the `include/dt-bindings/pinctrl/` block (neighbours
  `lochnagar.h`, `keystone.h`, `k210-fpioa.h`) —
  `fleet-search read https://packages.fedoraproject.org/pkgs/kernel/kernel-devel/fedora-43-updates.html`,
  `ok:true http:200`. That header is **not** part of the v10 series (its pinctrl patch 7/9
  adds only `drivers/pinctrl/mediatek/pinctrl-mtk-mt6392.h`), so it landed separately from
  the earlier Parent/Packett work.
- **GitHub API was unauthenticated this run** (no usable token — see ISSUE-LOG), so the exact
  out-of-tree repo/commit could not be pinned from here. The driver is known to live in
  MediaTek's **MT8167/MT8516 BSP (Linux 4.4/4.9/4.14-class) kernels** and in vendor trees
  such as the AOSP/Lenovo MT8167 sources the wiki references. **Physical/Frank follow-up:**
  a GitHub token would let a follow-up ticket locate and link the exact BSP driver files.

## Upstream status: the Scorcia v10 series (added by wq#4129)

**This is the load-bearing finding the original pass missed.** There is a complete,
actively-reviewed upstream submission for the exact driver stack this doc proposed to build.

- **Series:** `[PATCH v10 0/9] Add support for MT6392 PMIC`, Luca Leonardo Scorcia,
  posted to `linux-mediatek` 2026-09-02.
- **Thread:** `https://lore.kernel.org/linux-mediatek/20260902193351.185771-1-l.scorcia@gmail.com/`
  (per-patch msgids are that id with the trailing `-1` replaced by `-(n+1)`: the mfd patch
  5/9 is `…185771-6-…`, the regulator patch 8/9 is `…185771-9-…`).
- **Lineage (from the cover letter):** built on Fabien Parent's never-merged patches plus
  Val Packett's Jan-2025 resubmission, with the old review comments addressed, `dtbs_check`
  fixes, and a new pinctrl device. So this is the *third* generation of the same work — not a
  speculative RFC.
- **Author's own characterisation:** the MT6392 is "yet another MT6323/MT6397 variant",
  which independently confirms this doc's architecture framing.

| Patch | Subject | Notes |
|---|---|---|
| 1/9 | `dt-bindings: mfd: mt6397: Add MT6392 PMIC` | v10 dropped the `mediatek,mt6323` fallback; Reviewed-by deliberately not carried forward |
| 2/9 | `dt-bindings: input: mtk-pmic-keys: Add MT6392 PMIC keys` | |
| 3/9 | `regulator: dt-bindings: Add MediaTek MT6392 PMIC` | adds `dt-bindings/regulator/mediatek,mt6392-regulator.h` |
| 4/9 | `mfd: mt6397: Use MFD_CELL_* to describe sub-devices` | refactor-only prerequisite; mergeable on its own |
| 5/9 | `mfd: mt6397: Add support for MT6392 PMIC` | 5 files, +614; registers RTC/regulator/keys/pinctrl cells. **Reviewed-by: AngeloGioacchino Del Regno (Collabora)** |
| 6/9 | `input: keyboard: mtk-pmic-keys: Add MT6392 support` | power/home key |
| 7/9 | `pinctrl: mediatek: mt6397: Add MediaTek MT6392` | adds `drivers/pinctrl/mediatek/pinctrl-mtk-mt6392.h` |
| 8/9 | `regulator: Add MediaTek MT6392 regulator` | 4 files, +816; new `drivers/regulator/mt6392-regulator.c`. **Reviewed-by: AngeloGioacchino Del Regno** |
| 9/9 | `arm64: dts: mediatek: Add MediaTek MT6392 PMIC dtsi` | new `arch/arm64/boot/dts/mediatek/mt6392.dtsi`, plus a PMIC interrupt in the **pumpkin-common** include |

**Review state as of 2026-09-17** (last traffic on the list; the archive mirror used here is
current to 2026-10-16, and there is no v11 and no merge notice after that date): MFD
maintainer **Lee Jones** asked on-thread "Still waiting on Acks by the looks of things?" and
Scorcia answered "Unfortunately yes", noting patch 4/9 is independent and could go in alone.
So the series is **not rejected and not abandoned — it is parked on subsystem Acks**
(regulator/pinctrl/input/dt-bindings), with the two biggest patches already carrying a
MediaTek-platform maintainer's Reviewed-by.

**Why this matters for Miko:** the author tested on Xiaomi Mi Smart Clock X04G, Lenovo Smart
Clock 2 CD-24502F and Sony PlayStation Classic Mini SCPH-1000R — all MT8167/MT8516 class
devices — and patch 9/9 touches the **Pumpkin** common DTS include, which is the same DTS
family this doc proposes to derive Miko's from. Our board is inside the series' tested
population, not adjacent to it.

**How this was found (and why the first pass missed it):** `lore.kernel.org`,
`patchwork.kernel.org` and `elixir.bootlin.com` are all behind an Anubis proof-of-work wall
to `fleet-search read`, so the series is invisible to HTTP fetching. The public-inbox **git**
mirror is not walled: `git clone --shallow-since=2026-02-01 https://lore.kernel.org/linux-mediatek/0`
(72 MB) gives the full list archive, greppable offline with
`git log --grep=6392 -i`. See ISSUE-LOG #1386 (the technique) and #1388 (the empty-fetch
trap that can fake a negative result).

## Options, ranked

### Option A — Apply and test the upstream v10 series (recommended for PM)
Fetch the v10 series from lore (`b4 shazam 20260902193351.185771-1-l.scorcia@gmail.com`, or
apply the nine mboxes by hand), build it against our kernel, and bring up the `mt6392` node
on the Miko DTS using the series' own `mt6392.dtsi`. Work = rebase onto whatever kernel we
ship, wire our board's rails, run the boot/regulator probe, and feed test results **back to
the thread** (a Tested-by from another MT8167-class device is exactly what a series parked on
Acks needs). **Effort: low-medium** — the driver is written and reviewed; we are integrating and
testing, not authoring. Lands real suspend/DVFS/thermal, and if the series merges our delta
drops to zero.
*Risk:* v10 may churn before merge, so pin the msgid we tested and re-apply on each new
version rather than carrying a private fork.

### Option A2 — Port the BSP driver as an out-of-tree module (fallback if A is blocked)
MediaTek's BSP already contains a working mt6392 mfd + regulator + rtc + power-key stack.
Porting it forward to a modern kernel is tractable **because a near-identical sibling is
already upstream** (`mt6397`/`mt6323`): the register map, buck/LDO descriptor tables, and the
regmap-over-`pwrap` pattern all have a mainline template to diff against. Work = forward-port
API churn (regulator/mfd/of APIs moved since 4.x), author a DT binding
(`mediatek,mt6392` modelled on `mediatek,mt6397`), and wire the `mt6392` node under the
`pwrap` node in the Miko DTS. **Effort: medium.** Lands real suspend/DVFS/thermal.
**This was the original Option A; it is now strictly dominated by Option A** — it reproduces,
worse, work that already exists with maintainer review. Keep it only for the case where the
v10 series turns out not to apply to our kernel at all.

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
~~Highest effort, only worth it if upstreaming is a goal.~~ **Obsolete (wq#4129):** somebody
else is already carrying this, on its tenth revision. The useful remnant of this option is
*helping the existing series land* — test it on our hardware and reply `Tested-by:` to the
thread — which is already folded into Option A and costs us nothing extra.

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
2. **Then:** Option A as amended — apply Scorcia's v10 series (pin
   msgid `20260902193351.185771-1-l.scorcia@gmail.com`), add the `mt6392` node under `pwrap`
   using the series' `mt6392.dtsi`, and gate suspend/DVFS/thermal-DVFS enablement on that
   working. Report `Tested-by:` upstream. Fall back to the BSP hand-port (Option A2) only if
   the series genuinely won't apply.
3. **Fallback:** Option C (BSP kernel) only if full PM is required before A is ready.

**Follow-up tickets this implies**
- *(BUILD, new — replaces the GitHub-token one)* Mirror the nine v10 patches into the fleet
  from the lore public-inbox git clone (no token, no GitHub, no Anubis wall) and record the
  per-patch msgids, so a bring-up ticket can apply a pinned version offline.
- *(BUILD)* Obtain the Miko 3 / Pumpkin MT8167 DTS and enumerate every supply Linux touches,
  to build the Option-B fixed-regulator overlay.
- *(BUILD)* Apply + boot-test the v10 series (Option A), with a probe that exercises at least
  one **non**-always-on rail (acceptance probe that can fail: toggle a switchable LDO via
  `/sys/class/regulator/.../state` and confirm the voltage actually changes, so an inert
  always-on model cannot pass).
- *(watch)* Re-grep the lore mirror for `6392` monthly: a v11 or a merge changes the plan.
  ~~*(physical/Frank)* Provide a GitHub token…~~ — withdrawn; the BSP sources are no longer
  on the critical path, and lore-over-git needs no credential.

## Provenance / history note (wq#4129)

wq#4110 produced two byte-identical 136-line add-commits for this path:
`cb6839600a` (parent `21edb0c3`, 01:37:15 UTC) and `de8ace3616` (parent `34e3fe88`,
01:37:54 UTC), neither an ancestor of the other. **wq#4123's report describes both as landed
on `main`; that part is wrong.** Checked here:

- `git merge-base --is-ancestor de8ace3616 origin/main` → **yes**
- `git merge-base --is-ancestor cb6839600a origin/main` → **no**; `git branch -a --contains
  cb6839600a` lists only `fix/wq4110-miko-linux-investigate-mt6392-pmic-drive-a1`
  (and its `origin/` copy)
- `git log --full-history --diff-filter=A --oneline -- research/wq4110_mt6392_pmic_linux_blocker.md`
  → exactly one commit, `de8ace36`
- `git ls-files | grep -c wq4110_mt6392` → `1`

So history is **not** doubled: `main` has one add-commit and the tree has one copy. The
duplicate is a stale, never-merged lane commit sitting on the wq#4110 branch, which is
otherwise behind `main`. Nothing is rewritten (no force-push); the reconciliation is this
note, the rule that **this amended file is the single canonical version** — cite the path,
not either hash — and one cleanup candidate: delete the leftover
`fix/wq4110-miko-linux-investigate-mt6392-pmic-drive-a1` branch (local + `origin`) once its
PR is confirmed closed. Left undone here deliberately: no content lives only on that branch,
so deleting it is tidying, not a fix, and it is the branch owner's call.

## Sources
- `[PATCH v10 0/9] Add support for MT6392 PMIC` — Luca Leonardo Scorcia, linux-mediatek,
  2026-09-02: https://lore.kernel.org/linux-mediatek/20260902193351.185771-1-l.scorcia@gmail.com/
  (fetched via the public-inbox git mirror, `https://lore.kernel.org/linux-mediatek/0`,
  archive current to 2026-10-16; review state from Lee Jones' 2026-09-17 reply in-thread)
- Fedora `kernel-devel-7.2.10-100.fc43` package file listing (primary source for
  `mediatek,mt6392-pinfunc.h` in mainline 7.2):
  https://packages.fedoraproject.org/pkgs/kernel/kernel-devel/fedora-43-updates.html
- Nura wiki — MediaTek MT8167 (MT6392 PMIC "left unmerged"; its Mainlining-history section
  lists "mt6397: Add support for the MT6392 PMIC (reviewed but not merged yet)"):
  https://wiki.nura.eco/wiki/MediaTek_MT8167
- LWN Announcements patch roundups naming the series on 2026-03-17 / 08-20 / 09-02:
  lwn.net/Articles/1062574/, /1089646/, /1092276/
- Mainline MediaTek PMIC family (mt6397/mt6323 mfd+regulator+rtc, the port template):
  `drivers/mfd/mt6397-core.c`, `drivers/regulator/mt6323-regulator.c`, `drivers/rtc/rtc-mt6397.c`
- MediaTek PMIC wrapper (mt8167 transport, already mainline): `drivers/soc/mediatek/mtk-pmic-wrap.c`
- wq#4123 verify-pass judgment that triggered this amendment:
  `reports/wq4123_judgment_wq4110_missed_mt6392_upstream_series.md`
