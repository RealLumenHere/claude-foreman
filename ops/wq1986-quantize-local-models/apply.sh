#!/usr/bin/env bash
# wq#1986 — post-training quantization for local model routes (proposal 26e246075722,
# factory plan 0b80fe332894, yellow lane, config_edit; no factory applier → hand-apply).
#
# Switches the two remaining full-precision Ollama routes in LiteLLM to
# pre-quantized Q4_K_M GGUF tags:
#   isis-qwen3-32b: ollama/qwen3:32b -> ollama/hf.co/unsloth/Qwen3-32B-GGUF:Q4_K_M
#   mistral-7b:     ollama/mistral:7b -> ollama/hf.co/unsloth/Mistral-7B-Instruct-v0.3-GGUF:Q4_K_M
#
# Corrections vs the plan's draft apply_cmd, from the 2026-09-18 apply:
#   - The plan pulled BOTH models on localhost; the qwen route is served by Isis
#     (api_base 100.100.129.24:11434), so its pull must target Isis via OLLAMA_HOST.
#     A local pull would waste ~20GB and never fix the route.
#   - Model pulls are NOT part of this script's main path: the fworker guard denies
#     `ollama pull` from worker context (wq#1297 — bridge risk; manual off-peak only).
#     Run the pull-* subcommands manually off-peak. Until then the routes stay in
#     their pre-existing state: local ollama had NO mistral:7b tag and Isis was
#     unreachable, i.e. both routes were already dead — the tag edit regresses nothing.
#   - `ollama pull hf.co/...` can fail with 'realm host "huggingface.co" does not
#     match original host "hf.co"'; if it does, pull via huggingface.co/... and
#     `ollama cp` the result onto the hf.co/... name the config references.
#
# Rollback: cp /home/lumen/agent/litellm_config.yaml.bak.PREAPPLY \
#              /home/lumen/agent/litellm_config.yaml && docker restart litellm
set -euo pipefail

CFG=/home/lumen/agent/litellm_config.yaml
ISIS=http://100.100.129.24:11434
MISTRAL_TAG=hf.co/unsloth/Mistral-7B-Instruct-v0.3-GGUF:Q4_K_M
QWEN_TAG=hf.co/unsloth/Qwen3-32B-GGUF:Q4_K_M

pull_with_realm_fallback() {  # $1 = tag; honors OLLAMA_HOST
  ollama pull "$1" && return 0
  alt="huggingface.co/${1#hf.co/}"
  ollama pull "$alt" && ollama cp "$alt" "$1" && ollama rm "$alt"
}

case "${1:-apply}" in
  pull-local) pull_with_realm_fallback "$MISTRAL_TAG"; exit ;;
  pull-isis)  OLLAMA_HOST="$ISIS" pull_with_realm_fallback "$QWEN_TAG"; exit ;;
  verify)
    # post-apply / post-pull check: config routes quantized, gateway up, and each
    # quantized tag actually present on the host that serves it
    grep -q "model: ollama/$QWEN_TAG" "$CFG"
    grep -q "model: ollama/$MISTRAL_TAG" "$CFG"
    curl -fsS --max-time 5 http://localhost:4000/health/liveliness; echo
    ollama list | grep -F "$MISTRAL_TAG" || echo "PENDING: local pull ($MISTRAL_TAG) — run apply.sh pull-local off-peak"
    OLLAMA_HOST="$ISIS" ollama list | grep -F "$QWEN_TAG" || echo "PENDING: Isis pull ($QWEN_TAG) — run apply.sh pull-isis off-peak"
    echo "VERIFY_OK — config + gateway healthy; PENDING lines above list unpulled tags"
    exit ;;
  apply) ;;
  *) echo "usage: apply.sh [apply|pull-local|pull-isis|verify]" >&2; exit 64 ;;
esac

# precheck (idempotent): edit only if the old full-precision tags are still present
if grep -q 'model: ollama/qwen3:32b$' "$CFG"; then
  grep -c 'model: ollama/qwen3:32b$' "$CFG" | grep -qx 1
  grep -c 'model: ollama/mistral:7b$' "$CFG" | grep -qx 1
  cp "$CFG" "$CFG.bak.PREAPPLY"
  python3 - "$CFG" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
s = p.read_text()
s = s.replace('model: ollama/qwen3:32b', 'model: ollama/hf.co/unsloth/Qwen3-32B-GGUF:Q4_K_M')
s = s.replace('model: ollama/mistral:7b', 'model: ollama/hf.co/unsloth/Mistral-7B-Instruct-v0.3-GGUF:Q4_K_M')
p.write_text(s)
PY
else
  grep -q "model: ollama/$QWEN_TAG" "$CFG"  # already applied; fall through to restart
fi

docker restart litellm
# litellm needs ~30s to boot; connection-reset until then
curl -fsS --retry 10 --retry-delay 8 --retry-all-errors --max-time 5 \
  http://localhost:4000/health/liveliness
echo
echo "APPLY_OK — now run pull-local and pull-isis manually off-peak (wq#1297)"
