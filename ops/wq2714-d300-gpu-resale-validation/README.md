# wq#2714 — D300 GPU resale-validation harness

Headless stress+grade harness for AMD FirePro D300 (Tahiti/Pitcairn, GCN 1.0)
cards ahead of resale (wq#2444). Full SOP, including the manual visual gate
and how to read the grade log, lives at
`docs/wq2714_d300_gpu_resale_sop.md`. Run it via the wrapper at
`scripts/wq2714_run_resale_validation.sh` rather than calling
`orchestrator.sh` here directly.

## Files

- `orchestrator.sh` — runs the three headless stages in order, stops at
  first FAIL, appends to `grades/resale_grades.csv`.
- `bind_check.sh` — Vulkan (RADV) + OpenCL bind/visibility check.
- `vram_test.sh` — `memtest_vulkan` VRAM stress wrapper. memtest_vulkan has no CLI flags: the wrapper
  enumerates its device menu, picks the index whose `Bus=` matches the card under test, feeds it on stdin.
- `memtest_verdict.sh` — grades a memtest_vulkan log (FAIL / PASS / UNGRADED). PASS needs the tool's own
  `Standard 5-minute test PASSed` line and a confirmed device bus; UNGRADED maps to TOOL_MISSING, never a card FAIL.
- `compute_soak.sh` — `gpu-burn`/`clpeak` compute soak with thermal/clock
  telemetry via `monitor_sysfs.sh`.
- `record_visual_gate.sh` — records the manual FurMark2/glmark2 gate result
  (stage 2) against the same card/run.
- `lib_common.sh` — shared JSON/logging helpers, sourced by the above.
- `grades/resale_grades.csv` — the per-card resale-grade log.
- `guarded_run.sh`, `gpu_hold.sh` — wq#2842: run a GPU load with the low-power clamp lifted on the
  card under test, a live thermal abort, and a guaranteed put-back. `monitor_sysfs.sh` now samples every card.
- `../../scripts/wq2842_grade_on_rig.sh` — Lumen-side driver: preflight / run / collect; grades land in
  /home/lumen/re/common/gpu-grades/.
