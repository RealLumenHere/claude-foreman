#!/usr/bin/env bash
# wq#1295 — backup config.yaml before merging litellm_config_snippet.yaml,
# and verify the gateway still serves after. Run on the gateway node from a
# root/interactive session — this BUILD worker's sandbox cannot reach the
# live LiteLLM config or restart its service (see README.md).
#
# Usage:
#   backup_and_verify.sh backup   <config.yaml>
#   backup_and_verify.sh verify   [gateway_base_url]   # default http://127.0.0.1:4000
set -euo pipefail

cmd="${1:-}"

do_backup() {
  local cfg="$1"
  [[ -f "$cfg" ]] || { echo "backup_and_verify: $cfg not found" >&2; exit 2; }
  local stamp
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  local dest="${cfg}.bak.${stamp}"
  cp -p "$cfg" "$dest"
  echo "backup_and_verify: backed up $cfg -> $dest"
}

do_verify() {
  local base="${1:-http://127.0.0.1:4000}"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "${base}/health/readiness" || echo "000")
  if [[ "$code" == "200" ]]; then
    echo "backup_and_verify: OK ${base}/health/readiness -> 200"
    exit 0
  fi
  echo "backup_and_verify: FAIL ${base}/health/readiness -> ${code}" >&2
  echo "backup_and_verify: rollback with: cp -p <backup> <config.yaml> && systemctl restart <litellm-unit>" >&2
  exit 1
}

case "$cmd" in
  backup) do_backup "${2:?config.yaml path required}" ;;
  verify) do_verify "${2:-}" ;;
  *) echo "usage: $0 {backup <config.yaml>|verify [base_url]}" >&2; exit 2 ;;
esac
