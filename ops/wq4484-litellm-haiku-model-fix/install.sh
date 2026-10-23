#!/usr/bin/env bash
# wq#4484: idempotent live fix for /home/lumen/agent/litellm_config.yaml's
# lumen/claude-haiku model string. Backs up the file, swaps the bad model id
# for the verified one, restarts the litellm docker container, and leaves no
# credential material in command output (run everything through lumen-run,
# never grep/sed with the match printed to a terminal that isn't masked).
set -euo pipefail

F=/home/lumen/agent/litellm_config.yaml
BAD='model: anthropic/claude-haiku-5-5'
GOOD='model: anthropic/claude-haiku-4-5-20251001'

if grep -qF "$GOOD" "$F"; then
  echo "already fixed"
  exit 0
fi

if ! grep -qF "$BAD" "$F"; then
  echo "expected bad string not found — config has moved on, check manually" >&2
  exit 1
fi

TS=$(date +%Y%m%d_%H%M%SZ)
cp "$F" "${F}.bak.wq4484.${TS}"
sed -i "s#${BAD}#${GOOD}#" "$F"
docker restart litellm
sleep 6
docker ps --format '{{.Names}} {{.Status}}' | grep -i litellm
