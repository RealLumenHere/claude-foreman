#!/usr/bin/env bash
# wq#2714 stage 1b: VRAM stress via memtest_vulkan. Any reported error is a
# FAIL (this is a resale grading gate, not a tolerance test).
#
# wq#2842: the load now runs through guarded_run.sh (clamp lifted on the card under test, live
# thermal abort, guaranteed put-back). A rig that cannot lift or restore the clamp reports
# TOOL_MISSING -- the rig is not fit to grade -- never a card FAIL.
#
# VERIFY BEFORE FIRST PHYSICAL RUN: memtest_vulkan
# (github.com/GpuZelenograd/memtest_vulkan, confirmed to exist via
# fleet-search during harness build -- wq#2714) runs continuously and prints
# per-cycle error counts to stdout rather than a documented single-shot
# pass/fail flag we could verify without the binary in hand. This wrapper
# therefore does not trust exit code alone -- it greps the captured log for
# a nonzero error count. Run `memtest_vulkan --help` (or `-h`) on the rig
# once the binary is installed and adjust MEMTEST_VULKAN_ARGS below if the
# installed flags differ from a bare invocation. On a two-GPU host also
# confirm WHICH device it loads (GPU_CARDS only picks the card that is unclamped).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

OUTDIR="${1:?usage: vram_test.sh <outdir> <duration_seconds> [max_temp_c]}"
DURATION="${2:?usage: vram_test.sh <outdir> <duration_seconds> [max_temp_c]}"
MAX_TEMP_C="${3:-95}"
MEMTEST_VULKAN_BIN="${MEMTEST_VULKAN_BIN:-memtest_vulkan}"
MEMTEST_VULKAN_ARGS="${MEMTEST_VULKAN_ARGS:-}"
RESULT_FILE="$OUTDIR/vram_test.json"
RAW_LOG="$OUTDIR/vram_test.log"
mkdir -p "$OUTDIR"

if ! command -v "$MEMTEST_VULKAN_BIN" >/dev/null 2>&1; then
  write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "$MEMTEST_VULKAN_BIN not on PATH"
  log "vram_test -> TOOL_MISSING"
  exit 1
fi

log "running $MEMTEST_VULKAN_BIN for ${DURATION}s guarded (output: $RAW_LOG)"
set +e
# shellcheck disable=SC2086
"$HERE/guarded_run.sh" --outdir "$OUTDIR" --tag vram --max-temp-c "$MAX_TEMP_C" \
  --duration "$DURATION" --log "$RAW_LOG" -- "$MEMTEST_VULKAN_BIN" $MEMTEST_VULKAN_ARGS
rc=$?
set -e

case "$rc" in
  98|99)
    why=GPU_HOLD_FAILED
    [ "$rc" = 99 ] && why=GPU_RESTORE_FAILED
    write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "$why: rig cannot hold/restore the GPU power state, card not graded; see $OUTDIR/vram.guard"
    log "vram_test -> TOOL_MISSING (guard rc $rc)"
    exit 1 ;;
  97)
    write_result "$RESULT_FILE" "vram_test" "FAIL" "thermal abort: $(cat "$OUTDIR/.vram_thermal_abort" 2>/dev/null); see $OUTDIR/vram.guard"
    log "vram_test -> FAIL (thermal abort)"
    exit 1 ;;
esac

# rc 124 = the deadline fired, i.e. the tool ran the full duration clean.
# Any other nonzero rc means the tool crashed or exited early on its own --
# on a stress test that is itself a FAIL signal, not a shrug.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then
  write_result "$RESULT_FILE" "vram_test" "FAIL" "memtest_vulkan exited $rc before duration elapsed; see $RAW_LOG"
  log "vram_test -> FAIL (exit $rc)"
  exit 1
fi

if grep -qiE '[1-9][0-9]*[[:space:]]*error' "$RAW_LOG"; then
  write_result "$RESULT_FILE" "vram_test" "FAIL" "nonzero error count reported; see $RAW_LOG"
  log "vram_test -> FAIL (errors in log)"
  exit 1
fi

write_result "$RESULT_FILE" "vram_test" "PASS" "ran ${DURATION}s with no reported errors; see $RAW_LOG"
log "vram_test -> PASS"
