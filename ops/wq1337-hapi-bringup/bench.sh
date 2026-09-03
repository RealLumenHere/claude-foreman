#!/usr/bin/env bash
# wq#1337 hapi-1 bring-up benchmark.
#
# Resolves BC250_PROJECT.md's open 8.4-vs-38 tok/s discrepancy by measuring generation
# throughput at 512 / 2048 / 10000 token context against a live llama-server, logging
# tok/s + package power + throttle state per run. --soak adds a >60min sustained-load
# pass/fail check (fail = crash, hang, or output corruption in the 30-60min window).
#
# Requires: curl, jq, python3 (for the soak degeneration heuristic). Reads GPU power/temp
# from amdgpu hwmon sysfs; those readings are best-effort and blank if unavailable.

set -euo pipefail

HOST="127.0.0.1"
PORT="8080"
OUT_DIR="$(pwd)"
SOAK=0
SOAK_SECONDS=3900   # 65 min: covers the 60min bar plus margin
THROTTLE_TEMP_C=85
CONTEXTS=(512 2048 10000)
N_PREDICT=128

usage() {
  cat <<'EOF'
Usage: bench.sh [--host HOST] [--port PORT] [--out-dir DIR] [--soak]
                 [--soak-seconds N] [--throttle-temp C]

  --host HOST           llama-server host (default 127.0.0.1)
  --port PORT           llama-server port (default 8080)
  --out-dir DIR         where to write bench-results-<ts>.jsonl (default cwd)
  --soak                also run a sustained-load soak test (default: off)
  --soak-seconds N      soak duration in seconds (default 3900 = 65min)
  --throttle-temp C     temp_input threshold (C) counted as "throttling" (default 85)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --soak) SOAK=1; shift ;;
    --soak-seconds) SOAK_SECONDS="$2"; shift 2 ;;
    --throttle-temp) THROTTLE_TEMP_C="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

for bin in curl jq python3; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "missing required binary: $bin" >&2
    exit 1
  fi
done

BASE_URL="http://${HOST}:${PORT}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS_FILE="${OUT_DIR}/bench-results-${TS}.jsonl"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$1" >&2
}

hwmon_dir() {
  local d
  for d in /sys/class/drm/card*/device/hwmon/hwmon*; do
    if [[ -r "${d}/power1_average" || -r "${d}/temp1_input" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
  done
  return 1
}

read_power_w() {
  local dir microwatts
  dir="$(hwmon_dir 2>/dev/null || true)"
  if [[ -n "$dir" && -r "${dir}/power1_average" ]]; then
    microwatts="$(cat "${dir}/power1_average" 2>/dev/null || echo "")"
    if [[ -n "$microwatts" ]]; then
      python3 -c "print(round(${microwatts} / 1_000_000, 1))"
      return 0
    fi
  fi
  echo "null"
}

read_temp_c() {
  local dir millideg
  dir="$(hwmon_dir 2>/dev/null || true)"
  if [[ -n "$dir" && -r "${dir}/temp1_input" ]]; then
    millideg="$(cat "${dir}/temp1_input" 2>/dev/null || echo "")"
    if [[ -n "$millideg" ]]; then
      python3 -c "print(round(${millideg} / 1000, 1))"
      return 0
    fi
  fi
  echo "null"
}

is_throttling() {
  local temp
  temp="$(read_temp_c)"
  if [[ "$temp" == "null" ]]; then
    echo "unknown"
    return 0
  fi
  # integer compare via python3 (bash has no float comparison)
  python3 -c "print('true' if ${temp} >= ${THROTTLE_TEMP_C} else 'false')"
}

# Build a prompt of roughly $1 tokens by repeating a filler sentence.
# ~1.3 tokens/word for English filler text is a reasonable rough estimate; exactness
# doesn't matter here, only landing close to the target context depth.
build_prompt() {
  local target_tokens="$1"
  local words=$(( target_tokens * 3 / 4 ))
  python3 -c "
import sys
words = int(sys.argv[1])
filler = 'The quick brown fox jumps over the lazy dog near the river bank at dawn. '
out = (filler * ((words // 13) + 1))
w = out.split()
print(' '.join(w[:words]))
" "$words"
}

run_one_context() {
  local ctx="$1"
  local prompt response prompt_tps predicted_tps power_before power_after temp throttle

  log "context=${ctx}: building prompt and requesting completion"
  prompt="$(build_prompt "$ctx")"
  power_before="$(read_power_w)"

  response="$(curl -sS --max-time 300 "${BASE_URL}/completion" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg prompt "$prompt" --argjson n_predict "$N_PREDICT" \
      '{prompt: $prompt, n_predict: $n_predict, cache_prompt: false}')")"

  power_after="$(read_power_w)"
  temp="$(read_temp_c)"
  throttle="$(is_throttling)"

  prompt_tps="$(echo "$response" | jq -r '.timings.prompt_per_second // "null"')"
  predicted_tps="$(echo "$response" | jq -r '.timings.predicted_per_second // "null"')"

  jq -n \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson ctx "$ctx" \
    --argjson n_predict "$N_PREDICT" \
    --arg prompt_tps "$prompt_tps" \
    --arg predicted_tps "$predicted_tps" \
    --arg power_before_w "$power_before" \
    --arg power_after_w "$power_after" \
    --arg temp_c "$temp" \
    --arg throttling "$throttle" \
    '{ts: $ts, phase: "context-sweep", context: $ctx, n_predict: $n_predict,
      prompt_tok_s: ($prompt_tps | tonumber? // null),
      generation_tok_s: ($predicted_tps | tonumber? // null),
      power_before_w: ($power_before_w | tonumber? // null),
      power_after_w: ($power_after_w | tonumber? // null),
      temp_c: ($temp_c | tonumber? // null),
      throttling: $throttling}' >> "$RESULTS_FILE"

  log "context=${ctx}: prompt_tps=${prompt_tps} generation_tps=${predicted_tps} temp_c=${temp} throttling=${throttle}"
}

run_soak() {
  local deadline start now elapsed prompt response text repeat_run fail_reason
  start="$(date +%s)"
  deadline=$(( start + SOAK_SECONDS ))
  fail_reason=""
  prompt="$(build_prompt 1024)"

  log "soak: starting, target duration ${SOAK_SECONDS}s"

  while true; do
    now="$(date +%s)"
    if [[ "$now" -ge "$deadline" ]]; then
      break
    fi
    elapsed=$(( now - start ))

    if ! response="$(curl -sS --max-time 60 "${BASE_URL}/completion" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n --arg prompt "$prompt" '{prompt: $prompt, n_predict: 256, cache_prompt: true}')")"; then
      fail_reason="request failed or hung at elapsed=${elapsed}s (server crash or hang suspected)"
      break
    fi

    text="$(echo "$response" | jq -r '.content // ""')"

    if [[ -z "$text" ]]; then
      fail_reason="empty completion at elapsed=${elapsed}s (possible crash/hang)"
      break
    fi

    # degeneration heuristic: same token/word repeated >=20 times in a row, or
    # non-UTF8 bytes in the response, is treated as a proxy for GDDR6-corrupted output
    # on a headless box with no framebuffer to show literal pixel artifacts.
    repeat_run="$(python3 -c "
import sys
text = sys.argv[1]
words = text.split()
run = 1
best = 1
for i in range(1, len(words)):
    if words[i] == words[i-1]:
        run += 1
        best = max(best, run)
    else:
        run = 1
print(best)
" "$text")"

    if [[ "$repeat_run" -ge 20 ]]; then
      fail_reason="degenerate repeated-token output at elapsed=${elapsed}s (run length ${repeat_run})"
      break
    fi

    if ! python3 -c "
import sys
try:
    sys.argv[1].encode('utf-8').decode('utf-8')
except UnicodeError:
    sys.exit(1)
" "$text"; then
      fail_reason="non-UTF8 output at elapsed=${elapsed}s"
      break
    fi

    if dmesg 2>/dev/null | grep -qE 'VM_CONTEXT1_PROTECTION_FAULT|amdgpu.*GPU reset|amdgpu.*ring.*timeout'; then
      fail_reason="GPU reset / protection fault seen in dmesg at elapsed=${elapsed}s"
      break
    fi

    jq -n \
      --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson elapsed_s "$elapsed" \
      --arg power_w "$(read_power_w)" \
      --arg temp_c "$(read_temp_c)" \
      --arg throttling "$(is_throttling)" \
      '{ts: $ts, phase: "soak", elapsed_s: $elapsed_s,
        power_w: ($power_w | tonumber? // null),
        temp_c: ($temp_c | tonumber? // null),
        throttling: $throttling}' >> "$RESULTS_FILE"

    sleep 5
  done

  if [[ -n "$fail_reason" ]]; then
    log "soak: FAIL - ${fail_reason}"
    jq -n --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg reason "$fail_reason" \
      '{ts: $ts, phase: "soak-verdict", verdict: "FAIL", reason: $reason}' >> "$RESULTS_FILE"
    return 1
  fi

  log "soak: PASS - ${SOAK_SECONDS}s with no crash, hang, GPU reset, or output corruption"
  jq -n --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson duration_s "$SOAK_SECONDS" \
    '{ts: $ts, phase: "soak-verdict", verdict: "PASS", duration_s: $duration_s}' >> "$RESULTS_FILE"
}

main() {
  log "bench.sh: target=${BASE_URL} results=${RESULTS_FILE}"

  if ! curl -sS --max-time 10 "${BASE_URL}/health" >/dev/null 2>&1; then
    echo "llama-server not reachable at ${BASE_URL}/health" >&2
    exit 1
  fi

  local ctx
  for ctx in "${CONTEXTS[@]}"; do
    run_one_context "$ctx"
  done

  if [[ "$SOAK" -eq 1 ]]; then
    run_soak
  fi

  log "bench.sh: done, results in ${RESULTS_FILE}"
}

main "$@"
