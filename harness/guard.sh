#!/usr/bin/env bash
# guard.sh — PreToolUse fail-closed guard + lifecycle event logger for foreman workers.
# BUILD_PLAN.md §T2a / design/T2A_WORKER_HARNESS_DESIGN.md §5. Referenced from
# harness/worker-settings.json's hooks block. NEVER remove a check to make a worker's
# life easier — deny classes here are the wall behind the settings-pack deny rules
# (which are prefix matchers and evadable via quoting / `bash -c` wrapping).
#
# Contract: hook JSON envelope on stdin. Exit 0 = allow. Exit 2 = block, reason on
# stderr (fed back to the model). ANY internal error, unparseable input, or unknown
# shape = exit 2. jq missing = exit 2.
set -u

EVENTS_DIR=${FOREMAN_EVENTS_DIR:-/home/lumen/foreman/events}
WORKER_ID=${FOREMAN_WORKER:-unknown}
# fworker passes the worktree root so the rm -rf scope check has something to compare
# against; a guard invoked outside fworker (manual test) just gets an empty scope.
WORKTREE_ROOT=${FOREMAN_WORKTREE_ROOT:-}

command -v jq >/dev/null 2>&1 || { echo "guard.sh: jq not found — fail-closed" >&2; exit 2; }

INPUT=$(cat)
[ -n "$INPUT" ] || { echo "guard.sh: empty stdin — fail-closed" >&2; exit 2; }
echo "$INPUT" | jq -e . >/dev/null 2>&1 || { echo "guard.sh: unparseable hook JSON — fail-closed" >&2; exit 2; }

jfield() { echo "$INPUT" | jq -r "$1 // empty" 2>/dev/null; }

log_event() {
  # {ts, worker, event, session_id, effort, cwd} — one JSONL line. Best-effort: a logging
  # failure must never itself cause a fail-closed block (mkdir/tee errors are swallowed).
  local event="$1"
  mkdir -p "$EVENTS_DIR" 2>/dev/null
  local session_id effort cwd ts
  session_id=$(jfield '.session_id')
  effort=$(jfield '.effort.level')
  cwd=$(jfield '.cwd')
  ts=$(date -u +%FT%TZ)
  jq -nc --arg ts "$ts" --arg worker "$WORKER_ID" --arg event "$event" \
    --arg session_id "$session_id" --arg effort "$effort" --arg cwd "$cwd" \
    '{ts:$ts, worker:$worker, event:$event, session_id:$session_id, effort:$effort, cwd:$cwd}' \
    >> "$EVENTS_DIR/$WORKER_ID.jsonl" 2>/dev/null
}

# ---- lifecycle mode: --lifecycle start|stop|end ----
if [ "${1:-}" = "--lifecycle" ]; then
  LIFECYCLE=${2:-}
  case "$LIFECYCLE" in
    start|stop|end) log_event "$LIFECYCLE"; exit 0 ;;
    *) echo "guard.sh: unknown --lifecycle value '$LIFECYCLE' — fail-closed" >&2; exit 2 ;;
  esac
fi

# ---- host scope: foreman workers run on lumen only ----
HOST=$(hostname 2>/dev/null)
if [ "$HOST" != "lumen" ]; then
  echo "guard.sh: BLOCKED — host scope check failed (hostname='$HOST', expected 'lumen')" >&2
  log_event "blocked_host_scope"
  exit 2
fi

TOOL_NAME=$(jfield '.tool_name')
[ -n "$TOOL_NAME" ] || { echo "guard.sh: missing tool_name — fail-closed" >&2; exit 2; }

block() {
  local reason="$1" snippet="$2"
  echo "guard.sh: BLOCKED — $reason" >&2
  mkdir -p "$EVENTS_DIR" 2>/dev/null
  jq -nc --arg ts "$(date -u +%FT%TZ)" --arg worker "$WORKER_ID" --arg event "blocked" \
    --arg reason "$reason" --arg snippet "${snippet:0:200}" \
    '{ts:$ts, worker:$worker, event:$event, reason:$reason, snippet:$snippet}' \
    >> "$EVENTS_DIR/$WORKER_ID.jsonl" 2>/dev/null
  exit 2
}

case "$TOOL_NAME" in
Bash)
  RAW_CMD=$(jfield '.tool_input.command')
  [ -n "$RAW_CMD" ] || { echo "guard.sh: Bash tool_input.command missing — fail-closed" >&2; exit 2; }
  # Scan BOTH the raw string and a whitespace-collapsed lowercase copy: collapsing alone
  # would miss checks that depend on original spacing/case, scanning raw alone would miss
  # tab/newline/multi-space evasions.
  NORM=$(printf '%s' "$RAW_CMD" | tr '\n\t' '  ' | tr -s ' ')

  for target in "$RAW_CMD" "$NORM"; do
    if printf '%s' "$target" | grep -qE '(^|[;&|[:space:]])(sudo|reboot|shutdown|poweroff|halt|init +[06])\b'; then
      block "denied verb (sudo/reboot/shutdown/poweroff/halt/init 0|6)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE 'systemctl +(stop|disable|mask)\b'; then
      block "systemctl stop/disable/mask denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bdd\b +[^|;]*of='; then
      block "dd with of= denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE -- '--remove-orphans'; then
      block "docker/compose --remove-orphans denied (never on Lumen)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bip +link +set\b'; then
      block "ip link set denied (never spoof Lumen MAC)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bmacchanger\b'; then
      block "macchanger denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE 'git +push +.*(--force\b|--force-with-lease\b|-f\b)'; then
      block "git push --force denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bmkfs(\.[a-z0-9]+)?\b|\bwipefs\b'; then
      block "mkfs/wipefs denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE 'claude +setup-token\b'; then
      block "claude setup-token denied (rotates the account refresh token, GH #48786)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '(^|[;&|[:space:]])(ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL)='; then
      block "in-command re-injection of ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '/home/lumen/\.secrets\b'; then
      block "denied path: /home/lumen/.secrets (wq#616 static oauth token lives there)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bdocker\b' && ! printf '%s' "$target" | grep -qE '\bdocker\b +(ps|logs)\b'; then
      block "docker denied except ps/logs" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\brm\b'; then
      HAS_R=0; HAS_F=0
      printf '%s' "$target" | grep -qE '\brm\b[^|;]*(-[a-zA-Z]*r[a-zA-Z]*\b|--recursive\b)' && HAS_R=1
      printf '%s' "$target" | grep -qE '\brm\b[^|;]*(-[a-zA-Z]*f[a-zA-Z]*\b|--force\b)' && HAS_F=1
      if [ "$HAS_R" = 1 ] && [ "$HAS_F" = 1 ]; then
        # rf rm is only ok inside the worker's own worktree or /tmp.
        SAFE=0
        for abs in $(printf '%s' "$target" | grep -oE '/[^ |;&"'"'"']+'); do
          case "$abs" in
            /tmp/*) SAFE=1 ;;
            *)
              if [ -n "$WORKTREE_ROOT" ]; then
                case "$abs" in
                  "$WORKTREE_ROOT"|"$WORKTREE_ROOT"/*) SAFE=1 ;;
                  *) SAFE=0; break ;;
                esac
              fi
              ;;
          esac
        done
        [ "$SAFE" = 1 ] || block "rm -rf outside worktree/tmp denied" "$RAW_CMD"
      fi
    fi
  done
  ;;
Read|Edit|Write)
  FILE_PATH=$(jfield '.tool_input.file_path')
  if [ -n "$FILE_PATH" ]; then
    case "$FILE_PATH" in
      *.env|*.credentials.json|*claude_credentials.md|/home/lumen/agent/.env|/home/lumen/agent/.env/*|*/.claude/*|/home/lumen/.claude/*|/home/lumen/.secrets|/home/lumen/.secrets/*)
        block "denied path: $FILE_PATH" "$FILE_PATH" ;;
    esac
  fi
  ;;
esac

exit 0
