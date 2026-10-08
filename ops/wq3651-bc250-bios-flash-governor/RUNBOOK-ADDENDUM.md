# wq#3651 — BC-250 BIOS flash + governor addendum (hapi-1)

Base runbook: `ops/wq1337-hapi-bringup/RUNBOOK.md` (verified in wq#1349, merged PR #252).
This file adds what that runbook does not cover (the raw BIOS chip flash and the
boot-param/governor deltas wq#3651 asks for) and **corrects** the ROCm/Ollama portion
of wq#3651's own ticket text against already-verified fleet research.

**Nothing in this file is live or executed.** hapi-1 is still on the bench awaiting
thermal pads (`lumen_state current hapi-1` → `hapi-1.bringup_eta`, since 2026-09-02) —
there is no networked, SSH-reachable hapi-1 to run any of phases 2-3 against yet, and
the phase-1 BIOS chip flash is a hands-on hardware act (programmer clips, 3.3V rail,
EFI shell) that a headless worker cannot perform. Both are tracked as a **non-blocking
physical task** per standing autonomy policy, not a gate — this addendum ships the
complete, ready-to-run procedure so execution is mechanical the day hands are on the
bench.

## Phase 1 — BIOS flash [HANDS, physical task, not executable by this worker]

1. **Backup first, always.** Read the stock chip with the CH341A programmer before
   writing anything — `flashrom -p ch341a_spi -r stock-backup-$(date +%F).bin`. Verify
   the dump is non-empty and re-readable (`flashrom -p ch341a_spi -v`) before touching
   the modded image. No backup, no flash.
2. **3.3V ONLY.** The BC-250's BIOS chip is a 3.3V SPI part; CH341A boards default to
   5V on some clone batches. Confirm the programmer's VCC jumper/switch is set to 3.3V
   and probe it with a meter before clipping on — 5V on a 3.3V chip is the brick
   mechanism this step exists to prevent.
3. Rename the modded image `BC250_3.00_CHIPSETMENU.ROM` → `Robin5.00` (matches the
   loader's expected filename check) and flash via EFI shell per the modded-BIOS
   project's own flash script (not reproduced here — ticket text names it as "research
   docs"; that document was not reachable from this worktree — `/home/lumen/projects/bc250`
   is outside this session's `add_dirs` grant, same gap as ISSUE-LOG #129/#141, unchanged
   as of this run).
4. Clear CMOS after flashing (jumper or battery pull) before first boot on the new BIOS.
5. In BIOS setup: set **UMA Frame Buffer = 512MB, Dynamic** (not Fixed) — same setting
   `ops/wq1337-hapi-bringup/RUNBOOK.md:98-103` already documents and justifies (pairs
   with GTT sizing in Phase 2). Set **IOMMU = Disabled.**

   Flag, not a fix: wq#1337's RUNBOOK instead passes `iommu=pt` (IOMMU **pass-through**,
   not disabled) at the kernel command line (`RUNBOOK.md:86`). A BIOS-level IOMMU
   disable makes that kernel param a no-op, which is harmless, but if the modded-BIOS
   project's own doc specifies "IOMMU disabled" for a reason beyond "simplest safe
   default" (e.g. a PCIe passthrough path RUNBOOK.md doesn't use), that reason isn't
   visible from this worktree. Confirm against the SOT (`BC250_PROJECT.md`) before
   flashing rather than guessing — both settings are individually safe, the question is
   only which one the modded BIOS project actually validated.

## Phase 2 — boot params, 40CU unlock, governor

40CU unlock: **unchanged from wq#1337** — use `ops/wq1337-hapi-bringup/RUNBOOK.md`
§2 verbatim (`duggasco/bc250-40cu-unlock`, verified live against GitHub in wq#1349).
Do not use "duggasco 40cu kernel patch" as wq#3651's ticket text names it — that repo
name was already checked and found not to exist (RUNBOOK.md:41-44).

Boot params — wq#3651 asks for `ttm.pages_limit=4194304` in addition to what
RUNBOOK.md:86 already sets:

```
amdgpu.ppfeaturemask=0xffffffff iommu=pt amdgpu.gttsize=14750 ttm.pages_limit=4194304
```

`ttm.pages_limit=4194304` (pages, 4KiB each) = 16GiB, the TTM graphics-memory-manager
cap for the full unified pool — complementary to, not a replacement for, `gttsize=14750`
(which sizes the GTT sub-allocation within that pool). Append both; neither alone covers
what the other does.

Governor: **same binary/package as wq#1337** (`cyan-skillfish-governor-smu`,
`filippor/cyan-skillfish-governor` branch `smu`, build/install steps in
`RUNBOOK.md:125-204` — unchanged). The pin target differs by ticket, and both are real,
reconcilable numbers rather than a conflict:

- wq#1337's RUNBOOK pins a **ceiling**: nothing reachable above 1800MHz/900mV
  (`RUNBOOK.md:156-193`), justified as the production inference-only cap.
- wq#3651 asks for **1500MHz / 850-900mV**, which matches the *sustained operating
  point* already on record from pre-boot cooling validation (`lumen_state current
  hapi-1` → `hapi-1.bringup_runbook`: "pre-boot cooling targets (1500MHz/900mV
  sustained point)", since 2026-08-09).

Read together: 1500MHz/850-900mV is the sustained safe-point this ticket wants added to
the `[[safe-points]]` table (between the existing 1400/750 and 1600/800 entries in
`RUNBOOK.md:176-182`); 1800MHz/900mV stays the ceiling entry. Add, don't replace:

```toml
[[safe-points]]
frequency = 1400
voltage = 750

[[safe-points]]
frequency = 1500
voltage = 875

[[safe-points]]
frequency = 1600
voltage = 800
```

(`875` splits wq#3651's 850-900mV range; adjust once a real card is on the bench and
`bench.sh`'s power/temp logging — `RUNBOOK.md:278-298` — shows where this point actually
lands thermally.)

## Phase 3 — Ollama/ROCm: do not do this, serve per wq#1337 instead

wq#3651's own ticket text (`wq show 3651`, note timestamped 2026-10-08 10:08:29, logged
*before* this ticket was dispatched) already flags this:

> Ticket prescribes HSA_OVERRIDE_GFX_VERSION=10.3.0 + ROCm + rocminfo gfx1013 ... gfx1013
> has no ROCm userspace, the 10.3.0 spoof is explicitly NOT advisable, production path is
> Vulkan/RADV (ROCm expt-viable only via akandr/bc250-rocm)

That matches `ops/wq1337-hapi-bringup/RUNBOOK.md` §7 independently: "ROCm is a HARD NO
— GFX1013 has no ROCm userspace support; `HSA_OVERRIDE_GFX_VERSION` silently corrupts
compute results, never use it" (`RUNBOOK.md:206-207`), and §8: "Ollama drops MoE
throughput ~60 → ~25 tok/s on this model family ... build and run `llama.cpp`'s
`llama-server` directly" (`RUNBOOK.md:221-224`). Two independent sources (this ticket's
own pre-dispatch note, and a separately-verified runbook) agree the ROCm+Ollama path in
this ticket's Phase 3 is wrong for this silicon.

**Do not run `rocminfo` / `rocm-smi` / `HSA_OVERRIDE_GFX_VERSION=10.3.0` /
`OLLAMA_KEEP_ALIVE` / `HIP_VISIBLE_DEVICES` on hapi-1 in production.** Serve via
`ops/wq1337-hapi-bringup/llama-server.service` (Vulkan/RADV) per RUNBOOK.md §7-§8
instead. If an experimental ROCm comparison is still wanted, it is only expt-viable via
`akandr/bc250-rocm` per the pre-dispatch note — treat it as a side-by-side bench
experiment against the Vulkan path, never as the thing the production governor/systemd
unit is tuned for, and never with `qwen2.5-coder:7b`/`qwen2.5:14b` via Ollama as the
ticket's model list names — the production model remains `RUNBOOK.md`'s
Qwen3.6-35B-A3B IQ3 pending wq#266 (`RUNBOOK.md §9-10`).
