# wq#2440 — Prior-Art Sweep: existing solutions, further-along projects, unapplied hardware mods

**Date:** 2026-09-24 · **Author:** fworker-wq2440 · **Depends on:** wq#2439 (real web search — now live via `fleet-search`)

## Why this exists

Frank, 2026-09-24: "there are already solutions to projects, projects further along
than us, hardware optimizations on systems we own that were never found." The pattern is
"Frank finds it in one search; the fleet never does." Root cause was wq#2439 (no real web
egress). With `fleet-search` (SearXNG/Google-CSE web, authenticated GitHub API, HN, SO,
reddit-read) now available, this is the one-time audit that stops weeks of reinventing.

**Method:** for each active project and each owned device, searched GitHub + web +
community for (a) an existing tool that does the job, (b) a project further along than us,
(c) a hardware mod/optimization we never applied. Findings cite URLs; adopt/stop-building
calls are per project. Exploit *mechanics* are deliberately left to the per-project
validation tickets — this report names tools and links, not step-by-step chains.

**Legend:** 🟢 ADOPT (exists, works, take it) · 🟡 EVALUATE (promising, validate first)
· ⚪ KEEP BUILDING (nothing prior-art beats what we have).

---

## PROJECTS

### QuestFree (Quest RE) — 🟢 ADOPT, stop building the exploit
- **`starseed12345/QuestStack`** — https://github.com/starseed12345/QuestStack — 203★,
  updated 2026-09-24, status **Released**. Automated root + bootloader unlock for Quest 1
  (latest firmware) and Quest 2 (web version). Ships a GUI, auto device detection, verified
  backups, SHA-256 integrity checks, slot handling, recovery logic. Desktop app for
  Win/Linux/macOS.
- Corroboration: Hackaday 2026-08-25 "Quest VR Headset Becomes Unlocked Hardware";
  vr.org confirms two independent unlock projects on Quest 1.
- **Call:** this is done and further along than anything we would write. Do **not** build a
  bootloader-unlock chain from scratch. wq#2441 already exists to validate + test the chain
  on our hardware — that ticket is the right home; adopt QuestStack as the base there.
- **Caveat for wq#2441:** verify our Quest's exact firmware build against QuestStack's
  supported list before running; unsupported builds require the documented USB-Update-Mode
  reflash first. Quest Pro is unsupported.

### FreeMiko / Miko3 (+ Miko Mini) — 🟡 EVALUATE existing, keep our deeper track
- **`0alex1010/Freeko`** — https://github.com/0alex1010/Freeko — 9★. A *no-exploit*
  method: sideload APKs (Nova launcher + nav-bar + rotation helper) via the Miko 3
  privacy-policy → Share → Bluetooth path, off an SD card. Gets you a usable custom
  launcher **without** unlocking anything.
- Community: r/hacking has two Miko 3 threads and r/robotics one on running your own code —
  low signal, mostly people asking the same question we already answered internally.
- **Call:** Freeko is a fast win for "make it run our stuff" and is further along than us on
  the *launcher* problem — worth a day to reproduce. But it does **not** achieve persistent
  ADB / partition-level control, which is what wq#1432 (blind adb-enable via mtkclient),
  wq#1429 (persist adb patch), wq#1904 (adb disables on reboot), wq#1879 (MikoUnchained
  custom ROM) and wq#1452 (Miko Mini T40 eMMC-boot) are after. **Keep those** — no prior art
  found that reaches partition/ROM depth on the MT8167A. Suggest a small ticket: "repro
  Freeko sideload path as the quick-launcher win" so we stop hand-rolling that layer.

### Screenpad Plus RTD (standalone secondary display) — 🟢 ADOPT the Linux stack
- **`lakinduakash/asus-screenpad-control`** — https://github.com/lakinduakash/asus-screenpad-control
  — 36★. GUI brightness control for the ScreenPad Plus **plus the `asus-wmi` kernel
  module** — the hard part.
- **`jkbno/zenbook-duo-screenpad`** — power the panel truly off/on, swap windows across
  screens on Linux.
- **`Ryxwaer/asus-screenpad-brightness-sync`** — systemd service to sync brightness.
- **`sumedhtare/asus-screenpad-ux8402`** — udev rules + systemd + one-command installer,
  touch→trackpad switching via virtual input devices.
- **Call:** the Linux enablement (kernel module, brightness, power, touch remap) is solved
  and packaged. Adopt these rather than reversing the panel ourselves. **Open question the
  repos don't answer:** driving the RTD panel *fully standalone with no laptop host* — none
  of these detach it from the Zenbook. If our goal is standalone-panel-off-an-SBC, that
  specific gap is still ours to build; the driver/brightness/touch layers are not.

### Nest RE (UART recon, wq#11) — ⚪ KEEP BUILDING (device-specific)
- No GitHub/web hit maps to our specific Nest unit's UART bring-up. Generic Nest-root
  material is old and model-specific. wq#11 is HARDWARE-gated (physical UART) and stays a
  physical task. Prior-art sweep found nothing to adopt; proceed with in-house recon.

### Memory system — 🟢 ADOPT Obsidian (Frank-confirmed), skim the agent-memory field
- Frank already confirmed **Obsidian** as the memory substrate. Our file-based
  `claude_memory.md` + `[[wikilink]]` scheme is already Obsidian-compatible markdown — good,
  keep it, and point the vault at it rather than inventing a store.
- Field scan (validate before adopting): `ccf/agentcairn` (58★), `rwnalds/engram` (11★),
  `2672243194/agentbrain` — agent long-term-memory frameworks. None obviously beats a
  git-versioned Obsidian vault we control; **do not migrate** off markdown for these. Flag
  as reading, not adoption.

### Dispatch / node registry / AI-operation — ⚪ KEEP, one thing to watch
- GitHub search for autonomous-agent work-queue orchestration returned only low-star,
  low-signal repos (`agent-orchestra` 7★, etc.). Nothing approaches our dispatcher
  (budget-aware cloud_ceiling, burn-rate clamp, lease/fence, capability registry). **Keep
  building** — this is genuinely ahead of public prior art.
- The one durable AI-ops adoption is the harness itself (Claude Code) + Obsidian for
  memory, both already in use. No framework swap warranted.

---

## OWNED HARDWARE

### AMD BC-250 blades (Hapi, wq#120 — blocked "no hardware yet") — 🟢 ADOPT the community docs
- **`mothenjoyer69/bc250-documentation`** — https://github.com/mothenjoyer69/bc250-documentation
  — the canonical community reference. Confirms: Ariel APU (cut-down PS5 SoC), 6× Zen 2 @
  ~3.49 GHz, 24 CU RDNA2 iGPU ('cyan-skillfish', gfx1013), GDDR6 unified, 220 W TDP,
  J1000 = 8-pin 12 V PCIe, auto-start jumper, cooling guidance (GDDR6 runs hot — cool the
  memory).
- **AMD BC250 Documentation site** — https://elektricm.github.io/amd-bc250-docs/ — has a
  **RADV driver** page and a **Debian setup** page — turnkey Linux bring-up.
- **`akandr/bc250`** — https://github.com/akandr/bc250 — setup guide.
- r/LocalLLaMA "Did anyone try AMD BC-250 for inference?" — community inference results.
- **Call:** our own note says ROCm is dead on gfx1013 and only Vulkan works
  (`research/LOCAL_MODEL_STRATEGY.md:366`). The community docs point the same way — **RADV/
  Vulkan is the supported path; stop chasing ROCm.** When the blades are physically wired,
  follow the elektricm Debian + RADV pages rather than improvising. This directly unblocks
  the *software* half of wq#120; the hardware wiring stays a physical task.

### MacPro6,1 + dual FirePro D700 (Tahiti) — 🟡 EVALUATE: GPU inference may be possible
- Our registry treats MacPro6,1 as **CPU-only** inference
  (`research/INFERENCE_NODE_REGISTRY.md:46`).
- Prior art contradicts that: r/ollama "**Full GPU Acceleration for Ollama on Mac Pro 2013
  (Dual FirePro D700)**" — https://www.reddit.com/r/ollama/comments/1rd5ely/ — reports
  working GPU-accelerated inference on exactly our GPUs. Also: Proxmox-on-2013-Mac-Pro
  writeups for virtualization/passthrough.
- **Call:** worth a spike. If the D700s (GCN Tahiti) can be driven for inference under
  Linux ROCm/Vulkan, our two idle GPUs per box change the local-model math. **Do not assume
  CPU-only.** Recommend a ticket: "validate D700 GPU inference on a MacPro6,1 per the
  r/ollama method; re-measure vs the CPU-only baseline in INFERENCE_NODE_REGISTRY."

### Intel i5-1145G7 / i5-1185G7 (Tiger Lake) — ⚪ no unapplied mod found
- No exotic unlock/mod surfaced beyond standard Tiger Lake tuning (TDP/PL1-PL2 limits,
  thermal). If these nodes are power/thermal-throttled, raising PL limits is the only lever
  and it's routine, not a "hidden mod." Nothing to adopt; note it as a tuning checklist item.

### D300 GPUs — folds into the D700 spike
- Same GCN family as the D700 (lower-end Tahiti/Pitcairn). Whatever the D700 inference spike
  concludes applies here at lower capacity. No separate prior art.

### RK3399 (SBC) — ⚪ minor tuning only
- Community shows RK3399/RK3399-T mainline Linux maturity and SBC overclocking (Armbian,
  SkatterBencher on the newer RK3588), but nothing RK3399-specific that we haven't
  effectively got. NPU is weak on RK3399; not an inference target. Keep as a utility node.

---

## Bottom line — what changes

**Stop building (adopt instead):**
1. Quest bootloader/root chain → **QuestStack** (via wq#2441). ✅ done upstream.
2. ScreenPad Plus Linux enablement (kernel module, brightness, power, touch) → the four
   `asus-screenpad-*` repos. Only the *fully-standalone-no-host* case stays ours.
3. BC-250 Linux bring-up → **RADV/Vulkan** per elektricm docs; abandon ROCm on gfx1013.
4. Memory store → **Obsidian** over our existing markdown; no custom store, no framework
   migration.

**New spikes worth a ticket (prior art says we're leaving capability on the table):**
- D700/D300 **GPU inference** on MacPro6,1 (registry currently says CPU-only — likely wrong).
- **Freeko** sideload path as the quick Miko-launcher win (separate from our deep RE track).

**Keep building (genuinely ahead of / absent from public prior art):**
- Dispatcher / node registry / capability system.
- Miko partition/ROM-depth control (wq#1429/1432/1452/1879/1904).
- Nest UART recon (wq#11, hardware-gated).
- Standalone ScreenPad-off-an-SBC (the host-detach gap).

**Process fix (the real win):** prior-art search is now the mandatory FIRST step of every
RE project / tool build / "how do we solve X", using `fleet-search {web,github,reddit,hn,so,read}`.
That is what wq#2439 delivered; this report is its first application.
