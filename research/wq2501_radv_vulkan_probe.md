# wq#2501 — Mesa RADV Vulkan on the Mac Pro Tahiti GPUs (probe verdict)

Run 2026-09-28 on **nephthys** (left running throughout, no reboot). Raw output:
`research/wq2501_probe_data/`.

## Verdict

**The Tahiti GPUs are usable for llama.cpp through Vulkan. `compute stack: none`
(`research/wq2076_macpro_gpu_recon.md`) is true for CUDA, ROCm and OpenCL and false for Vulkan.**
On a 1.7B model fully resident on one GPU, prefill was 3.2–3.5x the CPU and decode 1.5x.
That does not yet show a win for anything the fleet serves; see "What this does not show".

## Corrections to the ticket

- nephthys has **2x FirePro D500** (Tahiti LE, PCI 1002:679e), not D700, and **3 GB** each, not 6 GB.
  Vulkan reports **2.75 GiB device-local** per GPU (plus a 256 MiB BAR heap). The fleet owns no D700.
  Tahiti LE is the same GCN 1.0 family as the D700's Tahiti XT, so this is a valid proxy for it,
  not a measurement of it.
- Nothing in the fleet's serving set fits one GPU: qwen3:8b is 5.2 GB, qwen3:14b 9.3 GB. Two GPUs
  give 5.5 GiB total. Full residency is off the table; the only plausible use is prefill offload.

## Steps and evidence

1. **Enumeration.** After `apt install vulkan-tools mesa-vulkan-drivers clinfo`, `vulkaninfo
   --summary` lists both cards as `AMD Radeon HD 7800 Series (RADV TAHITI)`, Vulkan 1.3.335,
   Mesa 26.0.8. `shaderFloat16 = false`, `shaderInt8 = true`, no cooperative matrix, subgroup 64.
   `clinfo -l` lists **no** OpenCL platforms.
   - Run as an unprivileged user, RADV printed `Permission denied` on `/dev/dri/renderD128/129`
     and only `llvmpipe` (software) enumerated. That is a group problem (`nephthys` is not in
     `render`), not a hardware verdict; run as root or add the user to `render`.
2. **Benchmark.** Upstream prebuilt `llama-b11228-bin-ubuntu-vulkan-x64` (sha256 matches the
   release digest; the build ships an `ivybridge` CPU variant, so no AVX2 is needed).
   Model `Qwen3-1.7B-Q4_K_M.gguf` (Apache-2.0). `llama-bench -p 512 -n 128 -r 2 -fa 0 -b 512`,
   **no flash attention** (llama.cpp #20889). The CPU baseline used `-dev none -ngl 0 -t 12` so the
   GPU could not assist prompt batches.

| run | prefill pp512 (t/s) | decode tg128 (t/s) |
|---|---|---|
| CPU, 12 threads, ivybridge variant | 99.3 (sd 0.4) | 31.2 (sd 0.3) |
| Vulkan0, RADV Tahiti LE | 321 (sd 30.6) | 47.3 (sd 0.0) |
| Vulkan1, RADV Tahiti LE | 343 (sd 0.0) | 47.2 (sd 0.0) |

   Sanity check on the CPU number: scaled by parameter count (1.7B to 14B) it gives about 11 t/s,
   inside the 7.4–13.6 t/s prefill recorded in `research/inference_nodes.yaml:58,86`.
3. **Guard results.** Each run went through `guarded_run.sh` (wq#2842) inside a systemd unit
   with `ExecStopPost=gpu-lowpower.sh`. Thermal abort 85 C (GPU and CPU), plus an AER abort
   (more than 15 new `PCIe Bus Error` lines). No abort fired. Peak GPU 62 C, CPU 48 C. **0** AER
   lines during the runs and 0 in the hour before. Clamp read back `low/battery` on both cards after.

## What this does not show

- **No speedup for a model the fleet serves.** A 14B model can offload only part of its layers into
  5.5 GiB, so its prefill is bounded by the layers left on the CPU. The 3.2–3.5x is for a model that
  fits entirely; the partial-offload number is unmeasured.
- **Sustained thermals.** Each run lasted about 15 s. The chassis shares one thermal core between CPU
  and both GPUs and has overheated before when GPU state changed (`ISIS_POWEROFF_RCA.md`). A 5 min
  soak is needed before anything is left running.
- **The clamp.** The runs held the cards at `auto/performance`. Serving with GPU offload means
  lifting the fleet-wide `low` clamp (wq#2166) on that node for as long as it serves.
- **Decode headroom is small** (1.5x) because it is memory-bound, and fp16 is unavailable.
- Only nephthys was measured. hathor and isis carry D300s (Curacao XT); not measured here.
- nephthys now has Mesa 26.0.8 (three libraries upgraded from 26.0.3 by the install) while the other
  Macs are still on 26.0.3.

## Amendment

`research/DIGEST_Orchestation_1_md.md` section 1 said every GPU line is void because `compute stack:
none`. Amended there: true for CUDA/ROCm/OpenCL, not for Vulkan.

## Suggested next probe (not run, and optional)

**Context that limits its value:** `fleet.compute_direction` (Frank, 2026-08-10) says the Mac Pro 6,1
fleet is being wound down, at most two kept as spares, and that no new Mac Pro roles, upgrades or
purchases should be proposed. So this probe's main worth is correcting the record (Vulkan does work
on these cards) and settling whether a *kept* spare would benefit. It is not a case for building
GPU offload into the Mac Pros. No follow-up ticket was filed for that reason.

qwen3:8b Q4_K_M with `-ngl` split across both GPUs (about 5.2 GB in 5.5 GiB), no flash attention,
under `guarded_run.sh` with a 5 min soak, comparing pp512/pp2048 against the CPU path on the same
binary. That is the measurement that decides whether prefill offload is worth the clamp.
