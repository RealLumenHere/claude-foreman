# wq#3923: SoC boot-mode USB VID:PID mapping — comprehensive detection signatures

Goal: give the Target Device Wizard (wq#3901) and HAL (`ops/wq3809-limpet-hal/`) the
data they need to auto-identify a target device the moment it's plugged in, across every
SoC/vendor family named in the ticket. Deliverable is
`ops/wq3809-limpet-hal/usb_boot_signatures.toml` (102 entries, schema: `vid`, `pid` or
`class_code`, `vendor`, `soc_family`, `mode_name`, `recommended_tool`, `confidence`,
`notes`), with a small loader/lookup module (`usb_boot_signatures.py`) and tests
(`tests/test_usb_boot_signatures.py`, 8/8 passing).

## Sourcing note: prior-art search ran degraded, same outage as wq#3835 the day before

Per the fleet's research-first doctrine, `fleet-search web`/`github` were tried first
for exactly the queries this ticket needs (e.g. `"mtkclient BROM USB VID PID list"`,
`"mtkclient"` on github) before compiling anything from memory. Both are down, and both
are the **same pre-existing, already-logged outages** `research/WQ3835_AMD_CPU_TIER_RESEARCH.md`
hit the day before (2026-10-14):

- `fleet-search web` → `ok:false`, all four general engines unresponsive (`brave`:
  "Suspended: too many requests", `duckduckgo`: "CAPTCHA", `google cse`: "Suspended: too
  many requests", `startpage`: "Suspended: CAPTCHA"), fallback failing on no
  `BRAVE_API_KEY` — wq#2595, still open, not re-logged.
- `fleet-search github` → `401`, "no usable token (credential helper checked
  identity-map, github-frank.token, agent/.env and found none valid)" — same symptom
  already tracked, not re-logged.

Net: this table is compiled from this agent's training-corpus knowledge of the on-disk
device tables shipped in the open-source flashing tools the ticket itself names
(mtkclient, edl/qdl, rkdeveloptool/upgrade_tool, pyamlboot, sunxi-fel, Heimdall,
dfu-util, rpiboot/usbboot) and each silicon vendor's USB-IF VID assignment — **not**
independently re-verified against live upstream source this run. This is the same
caveat `WQ3835_AMD_CPU_TIER_RESEARCH.md` flagged for its DeepSeek-sourced AMD tier data;
here there is no council/LLM hop at all, just this agent's own knowledge, so the
caveat is in some ways more direct: treat it as a well-corroborated compilation, not a
live-verified one.

## Confidence methodology (this is not decoration — it gates real behavior)

Every entry carries `confidence` (0.0–1.0), which is **not** a live-verification score —
it is how independently-corroborated each pair is in the sourcing above. This maps
directly onto the Target Device Wizard's existing confidence gate
(`docs/target-wizard-spec.md:171-184`): `known` (≥0.85) proceeds automatically,
`probable` (0.4–0.85) proceeds but gates any device-energizing step behind an operator
confirm, `unknown` (<0.4) blocks active hardware action entirely. That gate already
exists and already assumes imperfect evidence — it is the reason a table compiled
without live re-verification is still safe to ship: a wrong guess at medium/low
confidence cannot, by the wizard's own design, drive an irreversible action without a
human in the loop.

Entries split roughly like this:
- **High (≥0.85, 20 entries).** Pairs independently repeated across enough of the
  tooling/documentation in this agent's training data that there is effectively no
  ambiguity: Qualcomm EDL (`05c6:9008`), Allwinner FEL (`1f3a:efe8`, the single pair
  covering every Allwinner chip ever made), STM32 DFU (`0483:df11`), Apple DFU
  (`05ac:1227`), the vendor-independent ADB/fastboot/DFU USB class codes, etc.
- **Medium (0.4–0.85, ~60 entries).** Real, named pairs (most of the Rockchip
  per-chip-generation MaskROM/Loader pairs, most of the TI/NXP/Samsung/Amlogic
  rows) that are plausible and specific but not cross-checked live this run.
- **Low (<0.4, ~20 entries).** Included for ticket-coverage completeness (older
  generations, composite-descriptor variants, OEM-VID wildcards) with an explicit
  "not independently cross-checked" note — these exist so HAL has *something* to match
  against rather than silently missing the device, and so the 100-entry floor is met
  honestly rather than by padding with near-duplicates.

One table (Rockchip, §3) also surfaces a real discrepancy against this repo's own
shipped code: `ops/wq3809-limpet-hal/hal.py`'s existing `target_device` plugin labels
`2207:330c` "Rockchip MaskROM"; the rkdeveloptool a/b(c) generation-pair convention this
research found instead reads it as the **Loader** stage (post-DRAM-init) of the RK3399
generation, with `2207:330a` as that generation's actual MaskROM pair. Flagged in the
TOML's own entry rather than silently "fixed" in `hal.py` — that plugin's label is
already field-shipped (wq#3809/wq#3819/wq#3814 all iterated on it), relabeling it on an
unverified research pass would be the wrong direction of authority.

## Per-vendor findings worth calling out (not just "see the table")

- **MediaTek, Amlogic, Allwinner all share one important shape**: none of them vary the
  recovery-mode PID by SoC model. One or two PIDs per vendor cover the *entire* chip
  lineup named in the ticket (MT6735 through MT8195 are `0e8d:0003`/`0e8d:2000`;
  every A/H/T-series Allwinner chip is `1f3a:efe8`). Chip identity comes from the
  tool's post-connect handshake, never the enumeration itself — this is a genuine
  finding, not a gap: a Target Device Wizard design that expects a 1:1 PID→SoC map for
  these three vendors would be wrong by construction.
- **Rockchip is the opposite**: VID is fixed (`2207`) but PID genuinely varies per chip
  generation and mode, so it is the one vendor in this table with real per-chip rows
  (18 of them) — and the one with the lowest average confidence, because that
  granularity is exactly what this run couldn't live-verify.
- **Broadcom has no USB flashing mode analogous to the others.** The only legitimate
  USB-boot surface is the Raspberry Pi's `rpiboot` ROM mode; router/STB firmware on
  Broadcom SoCs is recovered over UART/JTAG/TFTP, not USB. Documented as a finding in
  the TOML rather than papered over with a fabricated pair.
- **Realtek and HiSilicon likewise have no native-USB BootROM download mode** for the
  SoC lines the ticket names (Ameba IoT, router switch SoCs; Hi35xx IP-camera/STB SoCs)
  — recovery on both is UART or SPI-flash-desolder territory, which is why
  `firmware-extraction`/`firmware-static-analysis` (not a USB signature) are this repo's
  actual tools for those targets. The one native-USB pair listed for each (Realtek
  RTL8762 BLE SoC, Huawei/HiSilicon-Balong modems under Huawei's own VID `12d1`) is
  explicitly lower-confidence and flagged as the exception, not the rule.
- **Xiaomi has no chip-level USB signature of its own.** A Xiaomi phone's real
  recovery-mode signature is whichever of the Qualcomm EDL or MediaTek BROM rows
  matches its underlying SoC; the Xiaomi VID (`2717`) only appears for the
  userspace fastboot/ADB interfaces. Spreadtrum/Unisoc — a major budget-Xiaomi SoC
  vendor not named in the ticket but squarely in its spirit — got its own BootROM
  entry (`1782:4d00`) as bonus coverage.
- **Standard USB device classes are the right generalization, not a parallel table.**
  ADB/fastboot/DFU/CDC-ACM/mass-storage are *interface class/subclass/protocol* codes
  (`class_code` in the schema), independent of vendor VID entirely — matching on them
  catches every OEM at once, including ones with no row in this table. The
  vendor-specific ADB/fastboot PIDs (Google `18d1`, the OEM wildcards for Sony/
  Motorola/HTC/ZTE/LG/OnePlus/OPPO/Asus/Vivo/Meizu) are a secondary, lower-confidence
  signal layered on top — useful for "which OEM" display context, not for deciding mode.

## What this ticket does not do

It does not rewire `hal.py`'s `REGISTRY` or the wizard's IDENTIFY resolution order —
those are wq#3809/wq#3901's code, out of this research ticket's scope (per the
target-wizard-spec's own §7 boundary: the wizard composes HAL detection, it doesn't
redefine it). `usb_boot_signatures.py` ships a `lookup()` function ready for either to
call; wiring it in is the next build ticket's job.
