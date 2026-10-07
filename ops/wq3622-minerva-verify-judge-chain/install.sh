#!/usr/bin/env bash
# wq#3622 — repoint the dead verify-judge chain. Run as root on Lumen, detached:
#   /home/lumen/bin/lumen-run start --root --name wq3622-judge-chain -- \
#       /home/lumen/foreman/ops/wq3622-minerva-verify-judge-chain/install.sh
#
# This build worker has NO grant to read /home/lumen/agent, /home/lumen/minerva,
# or restart minerva-proposal.service (fleet-exec-policy.yaml allow.restart only
# lists isis:[ollama] and lumen:[lumen-messenger, lumen-pokemon-live]; agent/.env
# and minerva/ are deliberately off every read grant — wq#1467/wq#1466 credential
# surface). Same constraint as wq#3543/wq#897/wq#948/wq#1295/wq#1406/wq#1590/
# wq#2627/wq#2471: the bundle is staged here, hand-apply is the only path.
#
# Ships:
#   - minerva_verify.py: VERIFY_JUDGE_MODEL default cerebras-oss-120b (402 since
#     2026-08-19) -> deepseek-v4-flash; VERIFY_JUDGE_MODEL_2 default
#     deepseek-v4-flash -> qwen36-local. See the file's wq#3622 comment.
#   - agent_env_fragment.txt: the matching explicit overrides for
#     /home/lumen/agent/.env (VERIFY_JUDGE_MODEL, VERIFY_JUDGE_MODEL_2,
#     CONTINUAL_LEARN_MODEL, CONVO_LEARN_MODEL), upserted key-by-key — never a
#     wholesale file copy (wq#1617 lost-update lesson applied to .env, not just
#     the litellm yaml it was written for).
#
# Grep-gated, not hash-gated: this build worker cannot read the live file to
# compute a pre-image sha256 (no grant), so install.sh instead refuses to patch
# unless it finds the EXACT old default line verbatim — a safer fail-closed
# than guessing a hash. A miss means live already moved past this patch;
# investigate by hand, do not force it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE_PY=/home/lumen/minerva/minerva_verify.py
LIVE_ENV=/home/lumen/agent/.env
BACKUP_DIR=/home/lumen/backups/wq3622
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

OLD_PRIMARY="JUDGE_MODEL = os.getenv('VERIFY_JUDGE_MODEL', 'cerebras-oss-120b')"
NEW_PRIMARY="JUDGE_MODEL = os.getenv('VERIFY_JUDGE_MODEL', 'deepseek-v4-flash')"
OLD_SECONDARY="JUDGE_MODEL_2 = os.getenv('VERIFY_JUDGE_MODEL_2', 'deepseek-v4-flash')"
NEW_SECONDARY="JUDGE_MODEL_2 = os.getenv('VERIFY_JUDGE_MODEL_2', 'qwen36-local')"

if grep -qF "$NEW_PRIMARY" "$LIVE_PY" && grep -qF "$NEW_SECONDARY" "$LIVE_PY"; then
    echo "unchanged: $LIVE_PY already carries the wq#3622 defaults"
elif grep -qF "$OLD_PRIMARY" "$LIVE_PY" && grep -qF "$OLD_SECONDARY" "$LIVE_PY"; then
    cp -p "$LIVE_PY" "$BACKUP_DIR/$stamp.minerva_verify.py.bak"
    sed -i "s|$OLD_PRIMARY|$NEW_PRIMARY|; s|$OLD_SECONDARY|$NEW_SECONDARY|" "$LIVE_PY"
    python3 -c "import ast; ast.parse(open('$LIVE_PY').read())"
    echo "patched: $LIVE_PY (backup: $BACKUP_DIR/$stamp.minerva_verify.py.bak)"
else
    echo "ABORT: $LIVE_PY does not match either the expected old or new default line." >&2
    echo "Live drifted since this bundle was built from ops/wq2027's mirror copy; rebuild by hand." >&2
    exit 2
fi

upsert_env() {
    # $1 = KEY, $2 = VALUE, $3 = file. Replaces an existing "KEY=" line, else appends.
    local key="$1" val="$2" file="$3"
    if grep -qE "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}

cp -p "$LIVE_ENV" "$BACKUP_DIR/$stamp.agent.env.bak"
chmod 600 "$BACKUP_DIR/$stamp.agent.env.bak"
upsert_env VERIFY_JUDGE_MODEL      deepseek-v4-flash "$LIVE_ENV"
upsert_env VERIFY_JUDGE_MODEL_2    qwen36-local       "$LIVE_ENV"
upsert_env CONTINUAL_LEARN_MODEL   deepseek-v4-flash  "$LIVE_ENV"
upsert_env CONVO_LEARN_MODEL       deepseek-v4-flash  "$LIVE_ENV"
echo "upserted: 4 keys in $LIVE_ENV (backup: $BACKUP_DIR/$stamp.agent.env.bak)"

# minerva-proposal.service is long-running (loads agent/.env + minerva_verify.py
# once at start, per wq#1501/wq#1529) so it needs an explicit restart to pick up
# either change above. The oneshot timer units (minerva-promote, minerva-
# proposal-promoter) re-exec fresh at their next scheduled fire and need no
# restart.
systemctl restart minerva-proposal.service
sleep 2
systemctl is-active --quiet minerva-proposal.service \
    && echo "restarted: minerva-proposal.service is active" \
    || { echo "ABORT: minerva-proposal.service failed to come back active after restart" >&2; exit 3; }

# Confirm the restart actually closed the drift stale-env-audit would flag.
/home/lumen/bin/stale-env-audit --fail-on-drift minerva-proposal.service \
    --vars VERIFY_JUDGE_MODEL,VERIFY_JUDGE_MODEL_2,CONTINUAL_LEARN_MODEL,CONVO_LEARN_MODEL

echo "Next: run $HERE/verify_judge_langfuse.py to confirm one judge call lands in Langfuse."
