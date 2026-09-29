#!/usr/bin/env bash
# wq#2842: classify a memtest_vulkan v0.5.0 log for the resale grading gate.
#
# Markers below come from the v0.5.0 binary's own strings (`strings memtest_vulkan`) and the
# upstream Readme sample output, not from guesswork. The old vram_test.sh grep
# ('[1-9][0-9]*\s*error') does NOT match 'Error found. Mode X, total errors 0x3C7EC3 out of ...'
# nor 'memory/gpu ERRORS FOUND', so a card with real VRAM errors graded PASS.
#
# usage: memtest_verdict.sh LOG [EXPECT_BUS]        EXPECT_BUS like 0x06:00 (bus:device of the card)
# stdout: one line '<VERDICT> <detail>'
# exit  : 0 PASS | 1 FAIL (card fault evidence) | 2 UNGRADED (rig/tool problem, never a card FAIL)
#
# PASS needs POSITIVE evidence: the tool's own 'Standard 5-minute test PASSed' line. A log with no
# errors and no pass line (short run, hang, killed early) is UNGRADED, not PASS.
set -uo pipefail

LOG="${1:-}"; EXPECT_BUS="${2:-}"
[ -n "$LOG" ] || { echo "UNGRADED usage: memtest_verdict.sh LOG [EXPECT_BUS]"; exit 2; }
[ -r "$LOG" ] || { echo "UNGRADED no readable log: $LOG"; exit 2; }

# memtest_vulkan drives the console: strip ANSI escapes and carriage returns first.
CLEAN=$(mktemp); trap 'rm -f "$CLEAN"' EXIT
sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' -e 's/\r/\n/g' "$LOG" > "$CLEAN"

started=0
grep -qE '^[[:space:]]*[0-9]+ iteration\. Passed' "$CLEAN" && started=1
iters=$(grep -cE '^[[:space:]]*[0-9]+ iteration\. Passed' "$CLEAN")

# 1. Which device did it actually test? A wrong-card run says nothing about the card under test.
if [ -n "$EXPECT_BUS" ]; then
  line=$(grep -m1 -E 'Standard 5-minute test of [0-9]+: Bus=' "$CLEAN" || true)
  if [ -z "$line" ]; then
    echo "UNGRADED cannot confirm which device was tested (no 'Standard 5-minute test of N: Bus=' line); expected $EXPECT_BUS"
    exit 2
  fi
  got=$(printf '%s' "$line" | sed -nE 's/.*Bus=(0x[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}).*/\1/p')
  if [ "${got^^}" != "${EXPECT_BUS^^}" ]; then
    echo "UNGRADED WRONG_DEVICE tested $got, expected $EXPECT_BUS: $line"
    exit 2
  fi
fi

# 2. Card-fault evidence.
if m=$(grep -m1 -E 'Error found\.|memory/gpu ERRORS FOUND|Standard 5-minute test fail|First test passed, but THEN runtime error' "$CLEAN"); then
  echo "FAIL memtest_vulkan reported memory errors: $m"
  exit 1
fi

# 3. Runtime error: after the test started it is a card/driver fault (e.g. ERROR_DEVICE_LOST,
#    the Readme's own bad-card sample ends that way); before it started it is an init/env problem.
if m=$(grep -m1 -E 'Runtime error:' "$CLEAN"); then
  if [ "$started" = 1 ]; then
    echo "FAIL runtime error after $iters iterations: $m"
    exit 1
  fi
  echo "UNGRADED runtime error before the test started (env/driver, not a card verdict): $m"
  exit 2
fi
if m=$(grep -m1 -E 'early exit during init|INIT OR FIRST testing failed' "$CLEAN"); then
  echo "UNGRADED init failure (env/driver, not a card verdict): $m"
  exit 2
fi

# 4. Positive evidence required.
if grep -qE 'Standard 5-minute test PASSed' "$CLEAN"; then
  echo "PASS standard 5-minute test PASSed, $iters iterations, no errors reported"
  exit 0
fi
echo "UNGRADED no errors but no 'Standard 5-minute test PASSed' line ($iters iterations): run too short, hung, or killed early"
exit 2
