#!/usr/bin/env bash
# wq#2714: shared helpers for the D300 resale-validation stage scripts.
# Not meant to be executed directly -- sourced by bind_check.sh, vram_test.sh,
# compute_soak.sh, monitor_sysfs.sh, orchestrator.sh, record_visual_gate.sh.
set -euo pipefail

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2
}

# write_result <path> <stage> <result:PASS|FAIL|TOOL_MISSING> <detail>
write_result() {
  local path="$1" stage="$2" result="$3" detail="$4"
  mkdir -p "$(dirname "$path")"
  jq -n \
    --arg stage "$stage" \
    --arg result "$result" \
    --arg detail "$detail" \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{stage: $stage, result: $result, ts: $ts, detail: $detail}' > "$path"
}
