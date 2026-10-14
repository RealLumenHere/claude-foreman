#!/usr/bin/env bash
# wq#3849 — pull the four ticket models on M80s.
#
# NOT run by any fworker/BUILD-worker session: the guard denies `ollama pull` from
# worker context outright (wq#1297 — bridge risk; manual, off-peak only), same split
# ops/wq1986-quantize-local-models/apply.sh already uses for the isis/mistral routes.
# Run this by hand, in a privileged session, off-peak.
set -euo pipefail

MODELS=(
  "qwen3.6:35b-a3b"      # agent-fast, MoE, primary agentic
  "qwen2.5-coder:32b"    # agent-deep, deep coding
  "qwen3:32b"            # research
  "qwen2.5-coder:14b"    # quick, fast fallback
)

for m in "${MODELS[@]}"; do
  echo "== pulling $m =="
  ollama pull "$m"
done

ollama list
