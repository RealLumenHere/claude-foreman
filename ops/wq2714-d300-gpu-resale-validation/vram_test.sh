#!/usr/bin/env bash
# wq#2714 stage 1b: VRAM stress via memtest_vulkan. Any reported error is a
# FAIL (this is a resale grading gate, not a tolerance test).
#
# wq#2842: the load now runs through guarded_run.sh (clamp lifted on the card under test, live
# thermal abort, guaranteed put-back). A rig that cannot lift or restore the clamp reports
# TOOL_MISSING -- the rig is not fit to grade -- never a card FAIL.
#
# wq#2842 (verdict + device fixes, memtest_vulkan v0.5.0, verified against the binary's strings
# and the upstream Readme):
#  * memtest_vulkan has NO command-line flags. Device choice is an interactive menu that autoselects
#    device 1 after ~10 s. On a two-card rig plus llvmpipe a bare run would test device 1 no matter
#    which card the harness unclamped. So: enumerate the menu, pick the index whose Bus= matches the
#    card's PCI address, feed it on stdin, and have memtest_verdict.sh confirm the 'Standard
#    5-minute test of N: Bus=' line matches. Mismatch or card-not-listed = TOOL_MISSING, never a
#    card verdict.
#  * The old grep for '[1-9][0-9]*\s*error' missed 'Error found. ... total errors 0x...' and
#    'memory/gpu ERRORS FOUND' (false PASS on a bad card). Grading is memtest_verdict.sh now, and
#    PASS needs the tool's own 'Standard 5-minute test PASSed' line.
#
# Env: MEMTEST_VULKAN_BIN (default memtest_vulkan), GPU_CARDS (single cardN => device is pinned and
# verified; unset/multiple => left to autoselect, bus NOT verified, a warning is logged), DRM_ROOT.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

OUTDIR="${1:?usage: vram_test.sh <outdir> <duration_seconds> [max_temp_c]}"
DURATION="${2:?usage: vram_test.sh <outdir> <duration_seconds> [max_temp_c]}"
MAX_TEMP_C="${3:-95}"
MEMTEST_VULKAN_BIN="${MEMTEST_VULKAN_BIN:-memtest_vulkan}"
DRM_ROOT="${DRM_ROOT:-/sys/class/drm}"
ENUM_SECONDS="${VRAM_ENUM_SECONDS:-8}"
RESULT_FILE="$OUTDIR/vram_test.json"
RAW_LOG="$OUTDIR/vram_test.log"
mkdir -p "$OUTDIR"

if ! command -v "$MEMTEST_VULKAN_BIN" >/dev/null 2>&1; then
  write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "$MEMTEST_VULKAN_BIN not on PATH"
  log "vram_test -> TOOL_MISSING"
  exit 1
fi
BIN_ABS=$(command -v "$MEMTEST_VULKAN_BIN")

# cardN -> 0xBB:DD, the form memtest_vulkan prints in 'Bus=' (PCI bus:device, upper-case hex).
card_bus_token() {
  local pci
  pci=$(basename "$(readlink -f "$DRM_ROOT/$1/device")") || return 1
  [[ "$pci" =~ ^[0-9a-fA-F]{4}:([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.[0-9a-fA-F]$ ]] || return 1
  printf '0x%s:%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]^^}"
}

EXPECT_BUS=""; IDX=""
CARD="${GPU_CARDS:-}"
if [ -n "$CARD" ] && [[ "$CARD" != *[[:space:],]* ]]; then
  if ! EXPECT_BUS=$(card_bus_token "$CARD"); then
    write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "cannot read the PCI address of $CARD under $DRM_ROOT; device not pinned, card not graded"
    log "vram_test -> TOOL_MISSING (no PCI address for $CARD)"
    exit 1
  fi
  # Enumerate: the menu is printed before the 10 s autoselect. It also starts a test on device 1,
  # which this kill ends after ENUM_SECONDS (a few seconds of clamped-low load, no thermal risk).
  ENUM_DIR=$(mktemp -d); trap 'rm -rf "$ENUM_DIR"' EXIT
  ( cd "$ENUM_DIR" && timeout -s KILL "$ENUM_SECONDS" "$BIN_ABS" </dev/null >menu.txt 2>&1 ) || true
  sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' -e 's/\r/\n/g' "$ENUM_DIR/menu.txt" > "$ENUM_DIR/menu.clean"
  IDX=$(sed -nE "s/^[[:space:]]*([0-9]+): Bus=${EXPECT_BUS}[[:space:]].*/\1/Ip" "$ENUM_DIR/menu.clean" | head -1)
  if [ -z "$IDX" ]; then
    devs=$(grep -E '^[[:space:]]*[0-9]+: Bus=' "$ENUM_DIR/menu.clean" | tr -s ' ' | tr '\n' ';')
    write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "$CARD ($EXPECT_BUS) is not in the memtest_vulkan device list, card not graded. Listed: ${devs:-none (init failed?)}"
    log "vram_test -> TOOL_MISSING ($CARD $EXPECT_BUS not in device list)"
    exit 1
  fi
  log "vram_test: $CARD = $EXPECT_BUS = memtest_vulkan device $IDX"
else
  log "vram_test: WARNING GPU_CARDS='${CARD}' is not a single card: device left to memtest_vulkan autoselect, bus NOT verified"
fi

log "running $BIN_ABS for ${DURATION}s guarded (output: $RAW_LOG)"
set +e
if [ -n "$IDX" ]; then
  # stdin = a FIFO held open read+write by this shell, primed with the device index. It never hits
  # EOF, so a tool that keeps reading the console cannot abort on end-of-input, and there is no
  # lingering feeder process to outlive the load. The tool writes memtest_vulkan.log to its cwd.
  "$HERE/guarded_run.sh" --outdir "$OUTDIR" --tag vram --max-temp-c "$MAX_TEMP_C" \
    --duration "$DURATION" --log "$RAW_LOG" -- \
    bash -c 'cd "$1" || exit 96; f=$(mktemp -u "$1/.stdin.XXXXXX"); mkfifo "$f"; exec 3<>"$f"; rm -f "$f"; printf "%s\n" "$2" >&3; exec "$3" <&3' \
    _ "$OUTDIR" "$IDX" "$BIN_ABS"
else
  "$HERE/guarded_run.sh" --outdir "$OUTDIR" --tag vram --max-temp-c "$MAX_TEMP_C" \
    --duration "$DURATION" --log "$RAW_LOG" -- "$BIN_ABS"
fi
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

# Grade from the tool's own output (classifier), THEN look at the exit code: an init failure exits
# nonzero too and must be TOOL_MISSING, not a card FAIL.
set +e
verdict=$("$HERE/memtest_verdict.sh" "$RAW_LOG" "$EXPECT_BUS")
vrc=$?
set -e
case "$vrc" in
  1)
    write_result "$RESULT_FILE" "vram_test" "FAIL" "$verdict; see $RAW_LOG"
    log "vram_test -> FAIL ($verdict)"
    exit 1 ;;
  2)
    write_result "$RESULT_FILE" "vram_test" "TOOL_MISSING" "$verdict; card not graded, see $RAW_LOG"
    log "vram_test -> TOOL_MISSING ($verdict)"
    exit 1 ;;
esac

# Verdict PASS. rc 124 = the deadline fired (ran the full duration clean) and 0 = the tool ended
# itself after its pass; any other nonzero rc means it crashed or was cut short, which on a stress
# test is itself a FAIL signal, not a shrug.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then
  write_result "$RESULT_FILE" "vram_test" "FAIL" "memtest_vulkan exited $rc after a clean standard test but before the duration elapsed; see $RAW_LOG"
  log "vram_test -> FAIL (exit $rc)"
  exit 1
fi

write_result "$RESULT_FILE" "vram_test" "PASS" "${verdict#PASS }; ran ${DURATION}s${EXPECT_BUS:+, device $IDX at $EXPECT_BUS}; see $RAW_LOG"
log "vram_test -> PASS"
