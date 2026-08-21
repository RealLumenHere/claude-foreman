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

  # ---- wq#729 R4: cap Bash output ----
  # Tool results are 39.5-51.5% of a build session's cost (research/CALL_AUDIT.md)
  # and Bash alone is 17.6% in the audited session; a single unbounded discovery/
  # dump command lands early and gets carried 150-200 calls. Rewrite the command
  # via PreToolUse `updatedInput` so the tool itself never returns more than
  # CAP_BYTES — confirmed supported in CLI 2.1.233 (code.claude.com/docs/en/hooks,
  # checked 2026-08-21: "Each field the event supports is honored, including
  # permissionDecision, additionalContext, updatedInput, and systemMessage").
  # research/CALL_AUDIT.md's tool-result real-vs-cl100k ratio is 1.63-1.81x on
  # this content mix (shell/code/JSON), so ~2000 bytes lands under, not over,
  # R4's ~1000-token target.
  #
  # Skip commands that mutate shell state the persistent session depends on
  # (cd/export/source/...) or background a job (&): piping subshells the left
  # side of the pipe, so a state change inside it would silently vanish instead
  # of persisting to the next Bash call. `source`/`.`-sourcing: only the `source`
  # keyword is matched — bare `. file` is deliberately NOT pattern-matched
  # because a naive check collides with the extremely common `find .`/`ls .`;
  # losing the (rare) bare-dot case is cheaper than losing capping on `find .`.
  # When unsure, don't cap: an uncapped result is recoverable, a lost `cd` is a
  # much worse class of bug.
  CAP_BYTES=${FOREMAN_BASH_OUTPUT_CAP_BYTES:-2000}
  SKIP_CAP=0
  for target in "$RAW_CMD" "$NORM"; do
    if printf '%s' "$target" | grep -qE '(^|[;&|[:space:]])(cd|export|source|alias|unalias|pushd|popd|declare|readonly|exec|eval|set|unset)\b'; then
      SKIP_CAP=1
    fi
    if printf '%s' "$target" | grep -qE '(^|[^&])&([^&]|$)'; then
      SKIP_CAP=1
    fi
  done

  if [ "$SKIP_CAP" = 0 ]; then
    WRAPPED=$(printf '{ %s\n} 2>&1 | head -c %s' "$RAW_CMD" "$CAP_BYTES")
    jq -nc --arg cmd "$WRAPPED" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:{command:$cmd}}}'
  fi
  ;;
Read|Edit|Write)
  FILE_PATH=$(jfield '.tool_input.file_path')
  if [ -n "$FILE_PATH" ]; then
    case "$FILE_PATH" in
      *.env|*.credentials.json|*claude_credentials.md|/home/lumen/agent/.env|/home/lumen/agent/.env/*|*/.claude/*|/home/lumen/.claude/*|/home/lumen/.secrets|/home/lumen/.secrets/*)
        block "denied path: $FILE_PATH" "$FILE_PATH" ;;
    esac
  fi

  # ---- wq#730 R3/R5/R6: bound Read, suppress repeat reads, reject dead reads ----
  # research/CALL_AUDIT.md: Read is the single most expensive tool in every
  # audited session, and 61 of 152 reads across the set pulled a whole file with
  # no offset/limit. Mechanism is the same confirmed PreToolUse `updatedInput`
  # lever wq#729 settled for Bash.
  if [ "$TOOL_NAME" = "Read" ] && [ -n "$FILE_PATH" ]; then
    # R6: a Read on a path that can't possibly succeed (missing, or a directory)
    # is a guaranteed error tool_result carried at full cost for the rest of the
    # session and teaches the model nothing a one-line deny doesn't already say.
    # Only the unambiguous case is caught here; anything permission- or
    # content-shaped is left to the real tool, which knows more than we do.
    if [ ! -f "$FILE_PATH" ]; then
      block "Read target is not a regular file — missing or a directory (wq#730 R6, avoids a guaranteed error entering the carry): $FILE_PATH" "$FILE_PATH"
    fi

    OFFSET=$(jfield '.tool_input.offset')
    LIMIT=$(jfield '.tool_input.limit')
    READ_LINE_CAP=${FOREMAN_READ_LINE_CAP:-300}
    EFF_LIMIT=${LIMIT:-$READ_LINE_CAP}
    EFF_OFFSET=${OFFSET:-0}

    # R5: suppress an exact-repeat read (same path/offset/limit) of a file that
    # hasn't changed on disk since this session last read it — two revisions of
    # the same file in context is a known way to confuse an editing agent, and
    # the tokens are pure waste since nothing changed.
    SESSION_ID=$(jfield '.session_id')
    MTIME=$(stat -c %Y "$FILE_PATH" 2>/dev/null)
    if [ -n "$SESSION_ID" ] && [ -n "$MTIME" ]; then
      CACHE_DIR=${FOREMAN_READ_CACHE_DIR:-$EVENTS_DIR/read_cache}
      mkdir -p "$CACHE_DIR" 2>/dev/null
      CACHE_FILE="$CACHE_DIR/$SESSION_ID.jsonl"
      if [ -f "$CACHE_FILE" ]; then
        HIT=$(jq -sr --arg p "$FILE_PATH" --arg o "$EFF_OFFSET" --arg l "$EFF_LIMIT" --arg m "$MTIME" \
          'map(select(.path == $p and .offset == $o and .limit == $l and .mtime == $m)) | length' \
          "$CACHE_FILE" 2>/dev/null)
        if [ -n "$HIT" ] && [ "$HIT" != "0" ]; then
          block "already read this session, file unchanged since (wq#730 R5): $FILE_PATH offset=$EFF_OFFSET limit=$EFF_LIMIT — reuse the earlier tool result instead of re-reading" "$FILE_PATH"
        fi
      fi
      jq -nc --arg p "$FILE_PATH" --arg o "$EFF_OFFSET" --arg l "$EFF_LIMIT" --arg m "$MTIME" \
        '{path:$p, offset:$o, limit:$l, mtime:$m}' >> "$CACHE_FILE" 2>/dev/null
    fi

    # R3: a Read that didn't ask for a limit gets one by default, same spirit as
    # R4's Bash cap — small/medium files pass through untouched (their line
    # count is under the cap), only genuinely large whole-file reads get bounded,
    # nudging the model toward grep/offset for the rest instead of one giant read.
    if [ -z "$LIMIT" ]; then
      if [ -n "$OFFSET" ]; then
        jq -nc --arg fp "$FILE_PATH" --argjson off "$OFFSET" --argjson lim "$READ_LINE_CAP" \
          '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:{file_path:$fp, offset:$off, limit:$lim}}}'
      else
        jq -nc --arg fp "$FILE_PATH" --argjson lim "$READ_LINE_CAP" \
          '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:{file_path:$fp, limit:$lim}}}'
      fi
    fi
  fi
  ;;
esac

exit 0
