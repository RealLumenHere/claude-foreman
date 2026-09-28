# wq#2842 design: make the wq#2714 GPU grading harness safe and meaningful on a Mac Pro 6,1

Written before code (instincts: design before build). Findings are from reading the harness and hathor read-only on 2026-09-28; nothing on hathor was changed.

## Defects found (evidence grade in brackets)

1. **Thermal limit is checked after the soak, never during it.** compute_soak.sh reads max temp from the CSV once the 7200 s tool has exited. [read: compute_soak.sh L56-72]. On MacPro6,1 the CPU and both GPUs share one thermal core; mbpfan reads coretemp only and is blind to GPU temp; hathor has an overheat-by-GPU-load history (2026-06-25). [lumen_state hathor.cooling_and_fan_control, wq#2501 notes]
2. **Telemetry watches one card.** monitor_sysfs.sh takes the first card with hwmon and breaks. hathor has two D300 (card1, card2). [read: monitor_sysfs.sh L22-28; sysfs: card1 hwmon2, card2 hwmon3]
3. **Grading runs under the low-power clamp.** Both cards sit at power_dpm_force_performance_level=low and power_dpm_state=battery (wq#2166, verified live 2026-09-28). A soak there grades idle clocks. Setting level alone is not enough: on SI parts power_dpm_state=battery biases the state selection, so both knobs must move. [gpu-lowpower.sh writes both]
4. **Nothing guarantees the clamp comes back.** A killed session (Commander recycles, SIGKILL) would leave a hot box unclamped. Class: restore lived in Claude hands, not in machinery.
5. **The grade lives only in the rig-side CSV.** Requested location /home/lumen/re/common/gpu-grades/ does not exist, and the rig cannot write to Lumen.

## Class and fix

Class: a stress harness that can push a shared-thermal-core chassis and has no live guard or guaranteed put-back.

- `guarded_run.sh` (new) wraps any GPU load: acquire hold, start monitor with live abort, run load in its own process group, kill group on abort or deadline, release hold in an EXIT trap. Exit codes: 97 thermal abort, 98 could not lift the clamp, 99 could not restore it, 124 deadline, else the load own rc.
- `gpu_hold.sh` (new): writes level+state for the cards under test only (GPU_CARDS), reads back every write, restores to the fleet contract low/battery (not "previous value"), then runs gpu-lowpower.sh --check when present. Fails closed.
- `monitor_sysfs.sh`: all cards, one row per card per sample, CPU package temp column, live abort after N consecutive samples over the limit, fails closed when abort is armed and no GPU temp is readable.
- vram_test.sh and compute_soak.sh run their load through guarded_run.sh. A hold or restore failure is reported as TOOL_MISSING (rig not fit), never as a card FAIL.
- orchestrator.sh: --card, passes the temp limit to vram_test, writes grade.json and copies it to GRADE_EXPORT_DIR.
- Machinery layer for SIGKILL: the Lumen-side runner starts the rig job with systemd-run and ExecStopPost=gpu-lowpower.sh, so systemd restores the clamp even if the script never gets to run a trap.

## Not verified (flagged, not asserted)

- memtest_vulkan and clpeak device selection on a two-GPU host. GPU_CARDS only controls which card is unclamped and monitored. First real run must confirm which Vulkan/OpenCL device each tool loads (bind_check records the device list).
- OpenCL on GCN 1.x under current Mesa (clpeak needs it). If clinfo reports zero devices, bind_check returns FAIL for a rig reason; that is a stack question, not a card verdict.
- gpu-burn is CUDA-only. It will never be on an AMD rig, so clpeak is the real compute tool here.

## Test plan (to failure)

Fake DRM/hwmon tree, no GPU: hold visible during run and restored after; abort on hot card kills the load; abort with no telemetry; SIGTERM to the wrapper restores; hold failure returns 98; deadline returns 124; second card hot; existing wq2444 rollup test still green.

## Rollback

Revert the PR. Harness is only invoked by hand or by the wq#2842 run; no timer uses it.
