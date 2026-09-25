# D300 GPU resale-validation SOP (wq#2714)

Grades AMD FirePro D300 (Tahiti/Pitcairn, GCN 1.0, Mac Pro 6,1 module) cards
good-vs-bad for resale, and records a per-card result. Built for the
hardware-resale plan in wq#2444. Designed to generalize to future parts
machines: swap the rig, the tool binaries, and `--expected-gpu`, keep the
three-stage shape.

## Rig assumptions

- A fleet Mac Pro 6,1 running Ubuntu with `amdgpu.si_support=1` (GCN 1.0
  needs this kernel param or amdgpu won't bind the card at all).
- RADV Vulkan + Mesa OpenCL (rusticl/Clover) userspace. **ROCm does not
  support Tahiti/Pitcairn** -- there is no `rocm-smi` here. All telemetry
  comes from `/sys/class/drm/card*/device/{hwmon,pp_dpm_sclk,gpu_busy_percent}`
  and `radeontop`, not ROCm tooling.
- Tools expected on PATH: `vulkaninfo`, `clinfo`, `memtest_vulkan`
  (github.com/GpuZelenograd/memtest_vulkan), and one of `gpu-burn` or
  `clpeak` for the compute soak. FurMark 2 and glmark2 for the manual gate
  (stage 2 only -- not needed headless).

## Stage 1 -- headless auto tests (no one present)

Entry point: `scripts/wq2714_run_resale_validation.sh` (thin wrapper -> real
implementation in `ops/wq2714-d300-gpu-resale-validation/`).

```
scripts/wq2714_run_resale_validation.sh \
  --serial <card-serial-or-asset-tag> \
  --vram-seconds 600 \
  --soak-seconds 7200 \
  --max-temp-c 95 \
  --expected-gpu PITCAIRN
```

Runs, in order, stopping at the first FAIL (no point soaking a card that
already failed VRAM):

1. **bind_check.sh** -- `vulkaninfo --summary` must mention
   `--expected-gpu` (default `PITCAIRN`; some D300 units report as `TAHITI`
   -- pass the right one), and `clinfo` must list at least one OpenCL
   device. FAIL here means the card/riser/slot didn't come up at the driver
   level -- stop, don't burn soak hours on it.
2. **vram_test.sh** -- runs `memtest_vulkan` for `--vram-seconds`. Any
   reported error count > 0, or the tool crashing/exiting early, is a FAIL.
   **Before the first physical run**, run `memtest_vulkan --help` on the
   rig and confirm/update `MEMTEST_VULKAN_ARGS` in the script if the
   installed binary's flags differ -- this was written from public
   docs/RE, never exercised against real hardware, per wq#2714 doctrine
   that assumption must be flagged, not asserted.
3. **compute_soak.sh** -- runs `gpu-burn` (preferred) or loops `clpeak` for
   `--soak-seconds`, while `monitor_sysfs.sh` samples temp/clock/busy% into
   `compute_soak_telemetry.csv` every 5s in the background. FAIL on: the
   compute tool exiting nonzero, max temp exceeding `--max-temp-c`
   (default 95C -- this is a placeholder; tune it once you have a few clean
   runs' worth of telemetry to set a real thermal-throttle line for this
   card generation), or an error/fail/segfault/xid pattern in the compute
   log.

Each stage writes `<outdir>/<stage>.json` (`{stage, result, ts, detail}`,
result one of `PASS`/`FAIL`/`TOOL_MISSING`) plus raw logs/CSVs alongside it,
and the orchestrator appends one row per stage to
`ops/wq2714-d300-gpu-resale-validation/grades/resale_grades.csv`.

A `TOOL_MISSING` result means the rig is missing a required binary, not
that the card failed -- install the tool and re-run before trusting a FAIL
next to it.

Headless PASS (all three stages green) prints the next command to run for
stage 2.

## Stage 2 -- manual visual gate (Frank attaches a monitor)

Only run this if stage 1 came back a full headless PASS -- it needs eyes on
a screen, so it's not worth doing on a card that's already failing.

1. Attach a monitor to the rig with the card under test seated.
2. Run **FurMark 2** (stress + benchmark mode, a few minutes). Watch for:
   tearing/corruption, flickering polygons, color-channel artifacts, driver
   reset/black-screen, or the display not coming up at all.
3. Run **glmark2** (default scene set). Same visual checklist -- glmark2's
   varied scenes catch some corruption patterns FurMark's single scene
   doesn't.
4. Record the result against the same card/run:

   ```
   ops/wq2714-d300-gpu-resale-validation/record_visual_gate.sh \
     --serial <same-serial-used-in-stage-1> \
     --run-ts <run_ts-printed-by-the-orchestrator> \
     --result PASS \
     --notes "no artifacts, ran furmark 5min + glmark2 default"
   ```

## Reading the per-card log

`ops/wq2714-d300-gpu-resale-validation/grades/resale_grades.csv` is an
append-only event log, one row per stage attempt: `serial, run_ts, stage,
result, notes, outdir`. For a given `(serial, run_ts)`:

- **FAIL grade**: any row with `result=FAIL`. Stop -- resell as
  parts/scrap, don't advertise as working.
- **PASS grade**: a `compute_soak,PASS` row present, AND a
  `visual_gate,PASS` row present for the same `(serial, run_ts)`. Advertise
  as tested-working.
- **Incomplete**: `compute_soak,PASS` with no `visual_gate` row yet --
  stage 2 hasn't been run. Not resale-ready either way.

## Repeating this for a future parts machine

Swap: the rig SSH target, `--expected-gpu` (match the card's
`vulkaninfo --summary` string), and the compute-soak tool if the new
card's stack supports something better than gpu-burn/clpeak (e.g. ROCm's
own tools, if the card generation supports ROCm unlike this one). The
three-stage shape (bind check -> VRAM stress -> compute soak -> manual
visual gate) and the CSV log schema stay the same.
