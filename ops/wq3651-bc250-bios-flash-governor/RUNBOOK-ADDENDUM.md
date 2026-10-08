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

- wq#1337's RUNBOOK pins a **ceiling**: nothing reachable above 1800MHz
  (`RUNBOOK.md:156-193`), justified as the production inference-only cap.
- wq#3651 asks for **1500MHz / 850-900mV**, which matches the *sustained operating
  point* already on record from pre-boot cooling validation (`lumen_state current
  hapi-1` → `hapi-1.bringup_runbook`: "pre-boot cooling targets (1500MHz/900mV
  sustained point)", since 2026-08-09).

Read together: 1500MHz/900mV is the sustained safe-point this ticket wants added to the
`[[safe-points]]` table, between the 1400MHz and 1600MHz entries in `RUNBOOK.md` §6;
1800MHz stays the ceiling *frequency*. Adding the point forces the voltages above it up
too, because a `[[safe-points]]` table must be non-decreasing in voltage as frequency
rises — see the note below — so 1600, 1700 and 1800 moved to 910/920/930 with it.
**wq#3706 has already applied this to `RUNBOOK.md` §6** — copy
that table as it stands; the blocks below are the same five rows, reproduced here so this
file's governor section is self-contained and its provenance is on the record:

```toml
[[safe-points]]
frequency = 1400
voltage = 750

[[safe-points]]
frequency = 1500
voltage = 900

[[safe-points]]
frequency = 1600
voltage = 910

[[safe-points]]
frequency = 1700
voltage = 920

[[safe-points]]
frequency = 1800
voltage = 930
# do not add points above 1800MHz — this box is inference-only, not gaming clocks
```

**Why these numbers, and why the ones they replace could not ship (wq#3706).** The
first draft of this file put 1500 at 875mV ("splits wq#3651's 850-900mV range") and left
1600 at 800mV, which would have made 1600MHz run on *less* voltage than 1500MHz — the only
non-monotonic step in the table, and an undervolt (hang / silently wrong compute) at the
clock just above the operating point. wq#3699 caught it; wq#3706 ruled. The replacements
are not interpolated guesses:

- **1500/900** is the measured point, three ways: the community BC-250 docs put 1500 at
  exactly 900mV in all four published `[[safe-points]]` tables
  (<https://elektricm.github.io/amd-bc250-docs/system/governor/>);
  `agents/work-queue/reports/425_hapi1_bringup_runbook.md:13` has 40CU@1500MHz/900mV at
  ~83°C sustained *indefinitely* on a stock heatsink; and `:79-83` names it the operating
  point, with "tune per-board upward from 900mV only if memtest_vulkan/llama-bench 10-min
  soak fails (+15-25mV steps, never >1075mV; prefer dropping freq)". 900mV is also the top
  of wq#3651's own requested range, so nothing is overridden to get it.
- **1600/910, 1700/920, 1800/930** come from the same published table (1600/910, 1700/920,
  1850/930). The shipped 800/850/900 trio was below the published curve at every point;
  with 1500 pinned at the measured 900mV it was also *inverted*. Lifting the top of the
  table is the only fix that keeps the measured point: pushing 1500 down under 800mV to
  preserve ordering would undervolt the one frequency hapi-1's whole cooling plan was
  validated at.
- **The low end is left alone on purpose.** The same published table puts 1000 at 800mV
  and 1175 at 850mV, where `RUNBOOK.md` ships 1175/700; other published tables use
  1000/700. That spread is real but it is not this defect — the shipped low end is
  monotonic and above the governor's hard 700mV floor (below 700mV the GPU pins to
  1500MHz and the governor is pointless), so no number was invented for it here. Open
  question for the bench, not for a doc edit.
- **Re-measure the power claim, do not assume it.** `RUNBOOK.md:195-198` derives "65W at
  1800MHz" from the SOT thermal table, not from the config — and that figure was taken
  against the old 900mV ceiling. 930mV at 1800MHz is ~+3% voltage, so expect single-digit
  percent more package power at the ceiling. Confirm it with `bench.sh`'s power logging
  (`RUNBOOK.md:278-298`) on the first real card, exactly as §6 already instructs, and
  adjust the 1700/1800 rows down if the thermals say so — downward is safe, it keeps the
  table monotonic.

**The table must be non-decreasing in voltage.** The governor is adaptive — it ramps
between `[[safe-points]]` pairs (`RUNBOOK.md:149-150`) — and every published BC-250 table
is monotonic, as ordinary GPU DVFS requires: a higher clock needs equal-or-more voltage,
never less. A dip means either an undervolt at the higher clock or undefined ramp
behaviour at the boundary, and on hardware that does not exist yet neither is testable.
`ops/tests/test_wq3706_safe_points_monotonic.py` enforces this over every
`[[safe-points]]` block in `ops/`, so the next edit to either file cannot reintroduce it.

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
