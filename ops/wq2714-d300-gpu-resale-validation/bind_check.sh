#!/usr/bin/env bash
# wq#2714 stage 1a: confirm the D300 (Tahiti/Pitcairn, GCN 1.0) binds and
# that the RADV Vulkan + OpenCL stacks see it. Frank's rig is a Mac Pro 6,1
# on Ubuntu with amdgpu.si_support=1; ROCm does not support this GPU
# generation, so RADV/clinfo visibility is the whole "did the card come up"
# check here -- there is no rocm-smi to fall back on.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

OUTDIR="${1:?usage: bind_check.sh <outdir> [expected_gpu_substring]}"
EXPECTED_GPU="${2:-PITCAIRN}"
RESULT_FILE="$OUTDIR/bind_check.json"
mkdir -p "$OUTDIR"

vulkan_ok=false
vulkan_detail="vulkaninfo not found"
if command -v vulkaninfo >/dev/null 2>&1; then
  vk_out=$(vulkaninfo --summary 2>&1) || true
  if echo "$vk_out" | grep -qi "$EXPECTED_GPU"; then
    vulkan_ok=true
    vulkan_detail="found '$EXPECTED_GPU' in vulkaninfo --summary"
  else
    vulkan_detail="vulkaninfo ran but did not report '$EXPECTED_GPU': $(echo "$vk_out" | head -c 400)"
  fi
fi

opencl_ok=false
opencl_detail="clinfo not found"
if command -v clinfo >/dev/null 2>&1; then
  cl_out=$(clinfo 2>&1) || true
  ndev=$(echo "$cl_out" | grep -c "Device Name" || true)
  if [ "${ndev:-0}" -gt 0 ]; then
    opencl_ok=true
    opencl_detail="clinfo reported $ndev OpenCL device(s)"
  else
    opencl_detail="clinfo ran but reported 0 devices: $(echo "$cl_out" | head -c 400)"
  fi
fi

if $vulkan_ok && $opencl_ok; then
  result=PASS
elif ! command -v vulkaninfo >/dev/null 2>&1 || ! command -v clinfo >/dev/null 2>&1; then
  result=TOOL_MISSING
else
  result=FAIL
fi

write_result "$RESULT_FILE" "bind_check" "$result" "vulkan: $vulkan_detail | opencl: $opencl_detail"
log "bind_check -> $result"
[ "$result" = "PASS" ]
