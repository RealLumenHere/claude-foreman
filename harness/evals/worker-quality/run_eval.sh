#!/usr/bin/env bash
# wq#1300: bridge-safe runner for the worker-quality promptfoo suite.
# Always invoke this instead of `promptfoo eval` directly — it is the only place the
# bridge-safety and concurrency-cap guards live.
#
# Guards (kb/tooling-findings-review.md §4.5, kb/fleet-upgrade-plan-review.md §4):
#  - LiteLLM base URL must be loopback: this suite must run co-located with the
#    gateway ON the inference node, never across the wifi bridge to a router.
#  - Concurrency capped (default 2, max 4) so the eval doesn't compete with real
#    inference traffic or the bridge.
#  - Never runs `ollama pull` and never touches a router/bridge address — this
#    script only ever talks to the local LiteLLM gateway.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:4000}"
MAX_CONCURRENCY="${WQ1300_MAX_CONCURRENCY:-2}"

case "$LITELLM_BASE_URL" in
  http://localhost:*|http://127.0.0.1:*|http://\[::1\]:*) ;;
  *)
    echo "REFUSED: LITELLM_BASE_URL=$LITELLM_BASE_URL is not loopback." >&2
    echo "This suite must run co-located with the LiteLLM gateway on the" >&2
    echo "inference node — it must never traverse the wifi bridge." >&2
    exit 1
    ;;
esac

if ! [[ "$MAX_CONCURRENCY" =~ ^[0-9]+$ ]] || [ "$MAX_CONCURRENCY" -lt 1 ] || [ "$MAX_CONCURRENCY" -gt 4 ]; then
  echo "REFUSED: WQ1300_MAX_CONCURRENCY=$MAX_CONCURRENCY out of allowed range [1,4]." >&2
  exit 1
fi

if [ "${1:-}" = "--check-only" ]; then
  echo "OK: LITELLM_BASE_URL=$LITELLM_BASE_URL max_concurrency=$MAX_CONCURRENCY"
  exit 0
fi

cd "$HERE"
mkdir -p runtime
python3 gen_dataset.py --out runtime/dataset.yaml
echo "dataset written; invoking promptfoo (max-concurrency=$MAX_CONCURRENCY)" >&2
npx --yes promptfoo eval \
  --config promptfooconfig.yaml \
  --max-concurrency "$MAX_CONCURRENCY" \
  --no-cache

python3 analyze_results.py runtime/results.json
