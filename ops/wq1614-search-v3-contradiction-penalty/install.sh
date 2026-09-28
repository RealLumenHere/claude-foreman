#!/usr/bin/env bash
# wq#1614 — gate, then install, the search_v3 contradiction penalty.
#
# The order matters and is the whole point: this NEVER copies a file in before
# the golden-set gate has run on the live corpus. Any rise in trap@1 aborts the
# run with the live file untouched (wq#595).
#
# Idempotent: re-running on an already-patched tree stops at the drift gate with
# exit 3 and a message saying so.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
STAMP=$(date -u +%Y%m%d_%H%M%S)

LIVE=/home/lumen/minerva/lumen_recall2.py

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step
# -- in particular before the golden-set gate, which is far too heavy for a
# liveness timer. lumen_recall2.py is edited in place by later search work, so
# the contract is the penalty knob this ticket added, not byte-equality.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vcontains "$LIVE" 'MEMORY_V4_W_CONTRA'
  vcontains "$LIVE" 'note_contradictions'
  vdone
fi
GOLDEN=/home/lumen/minerva/eval/golden_v4_frozen_20260814.jsonl
GOLDEN_SHA=8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7
FIXTURE=/home/lumen/minerva/eval/fixtures/note_contradictions_20260913.json
# sha256 of /home/lumen/research/minerva-ref/lumen_recall2.py that lumen_recall2.py
# here was patched from (mirror read 2026-09-13). Recorded, not guessed.
BASE_SHA=31aa35044560a6512e6e466834974174b54a2a67d01b3418e5e91453348e3f84
W=${W_CONTRA:-0.5}

say() { printf '\n== %s\n' "$*"; }

say "0. preflight"
for f in "$LIVE" "$GOLDEN" "$FIXTURE" /home/lumen/minerva/eval/recall_bench.py; do
  [ -r "$f" ] || { echo "MISSING: $f"; exit 4; }
done
[ "$(sha256sum "$GOLDEN" | cut -d' ' -f1)" = "$GOLDEN_SHA" ] || {
  echo "REFUSING: golden set is not the frozen 20260814 snapshot"; exit 4; }

say "1. drift gate"
live_sha=$(sha256sum "$LIVE" | cut -d' ' -f1)
if [ "$live_sha" != "$BASE_SHA" ]; then
  cat <<EOF
REFUSING: live $LIVE does not match the patch base.
  live   $live_sha
  base   $BASE_SHA
Either the penalty is already installed, or another lane moved the file. Re-derive
the patch against the current file (lumen_recall2.wq1614.diff is the change) and
re-record BASE_SHA before retrying. Nothing was modified.
EOF
  exit 3
fi

say "2. reach diagnostic (no DB)"
python3 "$HERE/contra_penalty_eval.py" reach \
  --golden "$GOLDEN" --fixture "$FIXTURE" \
  --baseline-results "$(ls -t /home/lumen/minerva/eval/results_sched_*.json | head -1)" \
  --out "$HERE/reach_${STAMP}.json" >/dev/null

say "3. unit tests"
python3 "$HERE/test_contra_penalty.py"

say "4. adoption gate on the live corpus (two recall_bench runs, ~10 min)"
set +e
python3 "$HERE/contra_penalty_eval.py" gate \
  --staged "$HERE/lumen_recall2.py" --w-contra "$W" \
  --golden "$GOLDEN" --fixture "$FIXTURE" \
  --out "$HERE/gate_${STAMP}.json"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "GATE FAILED (exit $rc) — see $HERE/gate_${STAMP}.json. $LIVE untouched."
  exit 1
fi

say "5. install"
cp -a "$LIVE" "$LIVE.bak.${STAMP}_pre_wq1614"
install -m 0644 "$HERE/lumen_recall2.py" "$LIVE"
echo "installed $LIVE (backup: $LIVE.bak.${STAMP}_pre_wq1614)"
python3 - "$HERE/gate_${STAMP}.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"verdict: {r['verdict']}")
print(f"trap@1 {r['trap_at1_rate']['baseline']} -> {r['trap_at1_rate']['treatment']} "
      f"(n={r['trap_at1_rate']['n_trap_rows']})")
if not r['trap_at1_moved']:
    print('\n!! trap@1 did not move. Per wq#1614 the remedy is the contradiction '
          'INDEX, not the weight: open a follow-up against the wq#1610 scan so it '
          'covers the notes the trap rows are made of. Do NOT retune W_CONTRA.')
PY
