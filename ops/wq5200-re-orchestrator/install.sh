#!/usr/bin/env bash
# wq#5200 Limpet Capability Router (re-orchestrate) install. Run as lumen on Lumen:
#   bash /home/lumen/foreman/ops/wq5200-re-orchestrator/install.sh
# No sudo, no services, no timers. Touches: the RE capability registry (merge + backup),
# maksoor:~/.local/lib/re-orchestrate/ (runner, sha-pinned), /home/lumen/re/evidence/.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin
REG=/home/lumen/re/toolkit-expansion/capability-registry.json

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/re-orchestrate"  "$BIN/re-orchestrate"
  vfile "$HERE/re-fingerprint"  "$BIN/re-fingerprint"
  vfile "$HERE/re-exec-runner"  "$BIN/re-exec-runner"
  python3 "$HERE/registry_merge_wq5200.py" --check >/dev/null 2>&1 || _vmiss "registry: orchestration blocks not merged ($REG)"
  if [ -x "$BIN/re-orchestrate" ]; then
    out=$("$BIN/re-orchestrate" runner-check 2>/dev/null || true)
    echo "$out" | grep -q '"in_sync": true' || _vmiss "runner on maksoor not in sync with $BIN (re-orchestrate runner-deploy)"
  fi
  [ -d /home/lumen/re/evidence ] || _vmiss "absent: /home/lumen/re/evidence"
  vdone
fi

for f in re-orchestrate re-fingerprint re-exec-runner; do
  [ -x "$BIN/$f" ] || { echo "missing $BIN/$f (land the lumen-bin wq#5200 PR first)"; exit 1; }
  cmp -s "$HERE/$f" "$BIN/$f" || echo "NOTE: $BIN/$f differs from the bundle copy (ok if bin moved on by PR)"
done

echo "--- registry merge (backs up to /data/lumen/backups/wq5200-registry-*.json)"
python3 "$HERE/registry_merge_wq5200.py"
python3 "$HERE/registry_merge_wq5200.py" --check

echo "--- runner deploy (maksoor:~/.local/lib/re-orchestrate, sha-pinned)"
ssh -o BatchMode=yes maksoor 'mkdir -p /data/re-work'
"$BIN/re-orchestrate" runner-deploy | grep -q '"in_sync": true' || { echo "runner deploy failed"; exit 1; }

mkdir -p /home/lumen/re/evidence

echo "--- smoke (plan only, no tool runs)"
"$BIN/re-orchestrate" plan /home/lumen/re/test-corpus/elf/x86_64/test_hello_x86_64 --no-hw \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("plan ok:", d["ranked_chains"][0]["chain"])'

echo "--- verify"
bash "$HERE/install.sh" --verify
echo "rollback: cp /data/lumen/backups/wq5200-registry-<ts>.json $REG ; ssh maksoor 'rm -rf ~/.local/lib/re-orchestrate'"
