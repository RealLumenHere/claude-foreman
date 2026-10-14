#!/usr/bin/env bash
# wq#3849 -- merge the M80s LiteLLM aliases (agent-fast/agent-deep/research/quick)
# into the live gateway config. Same shape as ops/wq1986-quantize-local-models/apply.sh:
# back up, edit, restart, verify. Run on Lumen, in a privileged session, under
# lumen-run or equivalent -- the live config is 0600 and credential-bearing
# (fleet-config/docs/RECOVERY.md section 4), outside any BUILD-worker's reach.
#
# Requires the real M80s Ollama api_base as $1 (e.g. http://100.x.x.x:11434) --
# resolve it from `lumen_state.py current m80s` once the node is onboarded. This
# script refuses to guess it.
#
# Rollback: cp /home/lumen/agent/litellm_config.yaml.bak.wq3849 \
#              /home/lumen/agent/litellm_config.yaml && docker restart litellm
set -euo pipefail

CFG="${WQ3849_CFG:-/home/lumen/agent/litellm_config.yaml}"
FRAGMENT="$(dirname "${BASH_SOURCE[0]}")/litellm-aliases.yaml"

case "${2:-apply}" in
  verify)
    curl -fsS --max-time 5 http://localhost:4000/health/liveliness; echo
    for alias in agent-fast agent-deep research quick; do
      grep -q "model_name: $alias" "$CFG" || { echo "MISSING: $alias"; exit 1; }
    done
    echo "VERIFY_OK -- all 4 M80s aliases present, gateway healthy"
    exit ;;
  apply) ;;
  *) echo "usage: apply.sh <m80s_api_base> [apply|verify]" >&2; exit 64 ;;
esac

API_BASE="${1:?usage: apply.sh <m80s_api_base> [apply|verify] -- e.g. http://100.x.x.x:11434}"

# wq#3863: a 1-arg invocation (e.g. the wq1986-style `apply.sh verify`) leaves $2 unset,
# so the case statement above falls through to apply with $1 (here the literal string
# "verify") silently accepted as API_BASE. The :? guard above never fires -- $1 is
# non-null -- so this is the only thing standing between a typo and a live config write.
case "$API_BASE" in
  http://*|https://*) ;;
  *) echo "usage: apply.sh <m80s_api_base> [apply|verify] -- api_base must start with http:// or https://, got: $API_BASE" >&2
     exit 64 ;;
esac

if grep -q "model_name: agent-fast" "$CFG"; then
  echo "APPLY_OK -- agent-fast already present, nothing to do (idempotent)"
  exit 0
fi

cp "$CFG" "$CFG.bak.wq3849"

python3 - "$CFG" "$FRAGMENT" "$API_BASE" <<'PY'
import sys
from pathlib import Path

cfg_path, fragment_path, api_base = sys.argv[1:4]
cfg = Path(cfg_path)
fragment_lines = Path(fragment_path).read_text().splitlines()

# Pull just the model_list entries (skip the file's own leading comments/header),
# substituting the real api_base for the placeholder.
block = []
in_list = False
for line in fragment_lines:
    if line.startswith("model_list:"):
        in_list = True
        continue
    if in_list:
        block.append(line.replace("PLACEHOLDER_FILL_FROM_lumen_state_py_once_onboarded", api_base))

text = cfg.read_text()
marker = "model_list:"
idx = text.index(marker) + len(marker)
new_text = text[:idx] + "\n" + "\n".join(block) + text[idx:]
cfg.write_text(new_text)
PY

docker restart litellm
curl -fsS --retry 10 --retry-delay 8 --retry-all-errors --max-time 5 \
  http://localhost:4000/health/liveliness
echo
echo "APPLY_OK -- run apply.sh <api_base> verify to confirm all 4 aliases resolve"
