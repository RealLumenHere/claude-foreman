#!/usr/bin/env bash
# wq#2842: Lumen-side driver for the wq#2714 GPU grading harness. Runs stage 1 on a rig
# (hathor, khepri) and lands the verdict in /home/lumen/re/common/gpu-grades/.
#
#   wq2842_grade_on_rig.sh preflight --rig hathor --card card1     # read-only, safe any time
#   wq2842_grade_on_rig.sh run       --rig hathor --card card1     # long: start under lumen-run
#   wq2842_grade_on_rig.sh collect   --rig hathor --unit U --serial S --run-ts T
#
# Options: --serial S (default <rig>-<pci addr of the card>)  --expected-gpu STR (PITCAIRN)
#          --vram-seconds N (600)  --soak-seconds N (7200)  --max-temp-c N (90)
#
# Design: reports/2842_d300_harness_design.md. The rig job runs as a transient systemd unit with
# ExecStopPost=+gpu-lowpower.sh, so the low-power clamp is restored by systemd even if the
# script is SIGKILLed. Nothing here touches the pilot-lane dispatcher: a dispatched worker has
# already consumed its slot, a session checks the cap by hand (dispatcher/state.json lane_counts).
#
# NOT verified on a real rig as of writing: the `run` and `collect` paths (preflight was run
# against hathor read-only). First real run must watch the journal of the unit.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BUNDLE="$HERE/../ops/wq2714-d300-gpu-resale-validation"
GRADES_DIR="${GRADES_DIR:-/home/lumen/re/common/gpu-grades}"

die() { echo "wq2842: $*" >&2; exit 1; }
say() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

cmd="${1:-}"; [ -n "$cmd" ] || die "usage: $0 preflight|run|collect --rig R --card cardN [...]"; shift
RIG=""; CARD=""; SERIAL=""; EXPECTED="PITCAIRN"; VRAM=600; SOAK=7200; MAXT=90; UNIT=""; RUN_TS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rig) RIG="$2"; shift 2 ;;
    --card) CARD="$2"; shift 2 ;;
    --serial) SERIAL="$2"; shift 2 ;;
    --expected-gpu) EXPECTED="$2"; shift 2 ;;
    --vram-seconds) VRAM="$2"; shift 2 ;;
    --soak-seconds) SOAK="$2"; shift 2 ;;
    --max-temp-c) MAXT="$2"; shift 2 ;;
    --unit) UNIT="$2"; shift 2 ;;
    --run-ts) RUN_TS="$2"; shift 2 ;;
    *) die "unknown option $1" ;;
  esac
done
[ -n "$RIG" ] && [ -n "$CARD" ] || die "--rig and --card are required"
case "$RIG" in hathor|khepri) ;; *) die "rig must be hathor or khepri (never isis: 09-19 power-loss history, wq#2842)" ;; esac
case "$CARD" in card[0-9]*) ;; *) die "--card must look like card1" ;; esac

rig() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$RIG" "$@"; }
# wq#2842 2026-10-20: both rigs were unreachable (ssh connect timeout) when this was last
# actually run. hathor auto-sleeps after 30min idle (lumen_state hathor.auto_sleep) and needs
# `/home/lumen/bin/wake-dispatch.sh wake hathor` before this will connect; khepri has been
# fully dark on tailscale+LAN since 2026-10-19T21:30Z with no remote plug (wq#4415, physical
# check required) -- do not retry-loop ssh against either without checking those first.
case "$RIG" in
  hathor) hint="wake it: /home/lumen/bin/wake-dispatch.sh wake hathor (auto-sleeps after 30min idle)" ;;
  khepri) hint="check wq#4415: khepri has been dark since 2026-10-19T21:30Z, no remote plug, needs physical check" ;;
esac
RUSER=$(rig 'id -un') || die "cannot ssh to $RIG -- $hint"
REMOTE_ROOT="$(rig 'echo $HOME')/pilots/wq2842"

pci_of_card() { rig "basename \$(readlink -f /sys/class/drm/$CARD/device)"; }

preflight() {
  local bad=0 load pci id t
  say "preflight $RIG $CARD"
  pci=$(pci_of_card) || die "cannot read $CARD on $RIG"
  id=$(rig "cat /sys/class/drm/$CARD/device/vendor /sys/class/drm/$CARD/device/device | tr '\n' ' '")
  say "  card $CARD = $pci  pci-id: $id"
  load=$(rig "cut -d' ' -f1 /proc/loadavg")
  if awk -v l="$load" 'BEGIN{exit !(l<2.0)}'; then say "  OK   load1=$load (<2.0)"; else say "  FAIL load1=$load, rig is busy"; bad=1; fi
  t=$(rig "systemctl list-units 'lumen-job-*' --state=running --no-legend 2>/dev/null | awk '{print \$1}' | tr '\n' ' '")
  if [ -z "${t// /}" ]; then say "  OK   no lumen-job-* running on the rig"; else say "  FAIL lumen-job units running: $t"; bad=1; fi
  t=$(rig "for h in /sys/class/drm/$CARD/device/hwmon/hwmon*; do cat \$h/temp1_input; done" | head -1)
  if [ -n "$t" ] && [ "$t" -lt 60000 ]; then say "  OK   $CARD temp $((t/1000))C at start"; else say "  FAIL $CARD temp '${t:-unreadable}' (need readable and <60C)"; bad=1; fi
  t=$(rig "for h in /sys/class/hwmon/hwmon*; do [ \"\$(cat \$h/name)\" = coretemp ] && cat \$h/temp1_input; done | sort -n | tail -1")
  if [ -n "$t" ] && [ "$t" -lt 70000 ]; then say "  OK   cpu package $((t/1000))C at start"; else say "  FAIL cpu package '${t:-unreadable}' (need <70C)"; bad=1; fi
  if rig "test -x /usr/local/sbin/gpu-lowpower.sh && sudo -n /usr/local/sbin/gpu-lowpower.sh --check >/dev/null 2>&1"; then
    say "  OK   fleet clamp verified before start (gpu-lowpower.sh --check)"
  else say "  FAIL gpu-lowpower.sh missing or clamp not at low before start"; bad=1; fi
  if rig "sudo -n true" 2>/dev/null; then say "  OK   NOPASSWD sudo"; else say "  FAIL sudo -n"; bad=1; fi
  for t in vulkaninfo clinfo memtest_vulkan clpeak; do
    if rig "command -v $t >/dev/null"; then say "  OK   tool $t"; else say "  MISS tool $t (install before run; stage would report TOOL_MISSING)"; bad=1; fi
  done
  if rig "command -v vulkaninfo >/dev/null"; then
    say "  Vulkan devices: $(rig "vulkaninfo --summary 2>/dev/null | grep -E 'deviceName' | sort | uniq -c | tr '\n' ';'")"
    say "  NOTE: confirm above lists '$EXPECTED' and which device each tool loads before trusting a two-GPU verdict"
  fi
  [ "$bad" -eq 0 ] || return 1
  say "preflight PASS"
}

collect() {
  [ -n "$UNIT" ] && [ -n "$SERIAL" ] && [ -n "$RUN_TS" ] || die "collect needs --unit --serial --run-ts"
  local dest="$GRADES_DIR/runs/${SERIAL}_${RUN_TS}" grade result
  mkdir -p "$dest" "$GRADES_DIR"
  rsync -a "$RIG:$REMOTE_ROOT/out/${SERIAL}_${RUN_TS}/" "$dest/" || die "rsync of run dir failed"
  grade="$dest/grade.json"
  [ -f "$grade" ] || die "no grade.json in $dest (rig job died before writing a verdict): $(rig "systemctl show $UNIT -p Result -p ExecMainStatus | tr '\n' ' '")"
  cp "$grade" "$GRADES_DIR/${SERIAL}_${RUN_TS}.json"
  result=$(jq -r .headless_grade "$grade")
  [ -f "$GRADES_DIR/index.csv" ] || echo "serial,run_ts,rig,card,headless_grade,failed_stage,expected_gpu" > "$GRADES_DIR/index.csv"
  jq -r '[.serial,.run_ts,.host,.card,.headless_grade,.failed_stage,.expected_gpu]|@csv' "$grade" >> "$GRADES_DIR/index.csv"
  if rig "sudo -n /usr/local/sbin/gpu-lowpower.sh --check >/dev/null 2>&1"; then
    say "clamp verified restored on $RIG"
  else
    say "CLAMP NOT RESTORED on $RIG -- run: ssh $RIG 'sudo /usr/local/sbin/gpu-lowpower.sh'"
    /home/lumen/bin/aq add wq2842 "clamp-$RIG" "GPU clamp not restored on $RIG after grading" "wq#2842 run ${SERIAL}_${RUN_TS}: gpu-lowpower.sh --check failed" >/dev/null 2>&1 || true
    return 3
  fi
  say "GRADE $result  -> $GRADES_DIR/${SERIAL}_${RUN_TS}.json"
  [ "$result" = PASS ]
}

run() {
  preflight || die "preflight failed, nothing started"
  local pci; pci=$(pci_of_card)
  SERIAL="${SERIAL:-${RIG}-${pci}}"
  RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
  UNIT="wq2842-grade-${CARD}-${RUN_TS}"
  say "syncing harness to $RIG:$REMOTE_ROOT/harness"
  rig "mkdir -p $REMOTE_ROOT/harness $REMOTE_ROOT/out"
  rsync -a --delete --exclude grades "$BUNDLE/" "$RIG:$REMOTE_ROOT/harness/"
  say "starting unit $UNIT (serial=$SERIAL card=$CARD max-temp=${MAXT}C)"
  rig "sudo -n systemd-run --unit=$UNIT --collect -p User=$RUSER -p WorkingDirectory=$REMOTE_ROOT \
    -p ExecStopPost=+/usr/local/sbin/gpu-lowpower.sh \
    $REMOTE_ROOT/harness/orchestrator.sh --serial $SERIAL --card $CARD --expected-gpu $EXPECTED \
      --vram-seconds $VRAM --soak-seconds $SOAK --max-temp-c $MAXT \
      --outdir $REMOTE_ROOT/out/${SERIAL}_${RUN_TS}"
  local deadline=$(( $(date +%s) + VRAM + SOAK + 1800 ))
  while rig "systemctl is-active --quiet $UNIT"; do
    [ "$(date +%s)" -lt "$deadline" ] || { rig "sudo -n systemctl stop $UNIT" || true; die "unit overran its deadline, stopped (ExecStopPost restores the clamp)"; }
    sleep 60
  done
  collect
}

case "$cmd" in
  preflight) preflight ;;
  run) run ;;
  collect) collect ;;
  *) die "unknown command $cmd" ;;
esac
