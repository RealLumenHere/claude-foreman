#!/usr/bin/env bash
# fworker_sandbox_slot.sh — ExecStart body for fworker-sandbox@.service (wq#3652).
# /var/fworker-rooted sibling of harness/wq_worker_slot.sh (wq#1182): that script
# hardcodes /home/lumen/foreman, which this unit's ProtectHome=yes makes EROFS for
# every user including fworker, so it cannot be reused as-is. Locking lives in the
# unit file; this script only resolves "what ticket does slot $1 run" and execs
# fworker.py for it.
set -euo pipefail

SLOT="${1:?usage: fworker_sandbox_slot.sh <slot>}"

: "${TICKET_ID:?fworker-sandbox slot ${SLOT}: TICKET_ID not set — write /var/fworker/.locks/fworker-sandbox-${SLOT}.env with TICKET_ID=<n> REPO=<path> before starting this slot}"
: "${REPO:?fworker-sandbox slot ${SLOT}: REPO not set — write /var/fworker/.locks/fworker-sandbox-${SLOT}.env with TICKET_ID=<n> REPO=<path> before starting this slot}"

exec python3 /var/fworker/foreman/harness/fworker.py run \
  --ticket "${TICKET_ID}" --repo "${REPO}" \
  ${MODEL:+--model "${MODEL}"} \
  ${EFFORT:+--effort "${EFFORT}"} \
  ${ARCHETYPE:+--archetype "${ARCHETYPE}"}
