#!/usr/bin/env bash
# wq_worker_slot.sh — ExecStart body for wq-worker@.service (wq#1182).
#
# Locking lives in the unit file (flock wraps this whole script); this script only
# resolves "what ticket does slot $1 run right now" and execs fworker.py for it. Job
# description arrives via EnvironmentFile (wq-worker-<slot>.env, written by whatever
# triggers this slot) so the template unit never needs per-start argument substitution.
set -euo pipefail

SLOT="${1:?usage: wq_worker_slot.sh <slot>}"

: "${TICKET_ID:?wq-worker slot ${SLOT}: TICKET_ID not set — write /home/lumen/.locks/wq-worker-${SLOT}.env with TICKET_ID=<n> REPO=<path> before starting this slot}"
: "${REPO:?wq-worker slot ${SLOT}: REPO not set — write /home/lumen/.locks/wq-worker-${SLOT}.env with TICKET_ID=<n> REPO=<path> before starting this slot}"

exec python3 /home/lumen/foreman/harness/fworker.py run \
  --ticket "${TICKET_ID}" --repo "${REPO}" \
  ${MODEL:+--model "${MODEL}"} \
  ${EFFORT:+--effort "${EFFORT}"} \
  ${ARCHETYPE:+--archetype "${ARCHETYPE}"}
