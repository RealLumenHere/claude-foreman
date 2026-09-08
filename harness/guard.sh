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
    # Leading context is "anything that is not part of a longer word": an explicit
    # [;&|[:space:]] class missed every verb sitting immediately after an opening
    # quote or paren — `ssh bes 'sudo ...'` slipped straight through. Disclosed in
    # reports/wq415_..., re-hit by wq#857, closed here (wq#879). Keeping _ . / -
    # out of the match keeps `my_sudo`, `./reboot.sh` and `--no-halt` from tripping.
    if printf '%s' "$target" | grep -qE '(^|[^[:alnum:]_./-])(sudo|reboot|shutdown|poweroff|halt|init +[06])\b'; then
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
    # Same leading-context class as the denied-verb scan above: the old
    # [;&|[:space:]] class saw nothing after an opening quote, so the assignment
    # wrapped in `ssh host '...'` or `bash -c "..."` re-injected the key unblocked
    # (wq#879 fixed only the verb scan, wq#1548 closes this twin).
    if printf '%s' "$target" | grep -qE '(^|[^[:alnum:]_./-])(ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL)='; then
      block "in-command re-injection of ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL denied" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '/home/lumen/\.secrets\b'; then
      block "denied path: /home/lumen/.secrets (wq#616 static oauth token lives there)" "$RAW_CMD"
    fi
    # Bash-side twin of the Read/Grep credential deny below: the credentials file, its
    # timestamped .bak copies, and the minerva context sections that mirror it (027 +
    # the sections.json index) sit inside dirs workers get via needs:dir.
    if printf '%s' "$target" | grep -qE 'claude_credentials\.md|/home/lumen/minerva/context/sections/[^ ]*credentials|/home/lumen/minerva/context/sections\.json'; then
      block "denied path: credential file/copy (claude_credentials.md*, minerva context credential sections)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bdocker\b' && ! printf '%s' "$target" | grep -qE '\bdocker\b +(ps|logs)\b'; then
      block "docker denied except ps/logs" "$RAW_CMD"
    fi
    # wq#1297: a worker running unattended (this hook only fires for fworker/cron
    # spawns, never a human's interactive session) is a bigger bridge threat than any
    # monitor — an ollama pull can be tens of GB, and unlike speedtest-tracker
    # (ISSUE-LOG #26) it was on no exclusion list. `docker pull` already dies on the
    # generic docker-denied rule above; podman and git-lfs have no such umbrella rule,
    # so each large-pull surface gets its own line here. These pulls are manual/
    # off-peak-only (Frank at a terminal), never something a headless worker decides
    # to run — there is no size threshold to compute here because there is no
    # allowed size; the refusal is unconditional in this context.
    if printf '%s' "$target" | grep -qE '\bollama\b +pull\b'; then
      block "ollama pull denied from cron/fworker context (wq#1297: large pulls are manual off-peak only — bridge risk)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bpodman\b +pull\b'; then
      block "podman pull denied from cron/fworker context (wq#1297: large pulls are manual off-peak only — bridge risk)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '\bgit\b.*\blfs\b +(pull|fetch)\b'; then
      block "git lfs pull/fetch denied from cron/fworker context (wq#1297: large pulls are manual off-peak only — bridge risk)" "$RAW_CMD"
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

    # ---- wq#900: PRIMARY trees are branch-frozen for workers ----
    # A worker's raw `git checkout` in /home/lumen/foreman put the live dispatcher
    # timers on a feature branch (audit 2026-08-23; same class hit /home/lumen/bin,
    # wq#867). lumen-pr already refuses --primary — this is the wall behind it: any
    # HEAD/branch-moving git verb is denied when the hook cwd is inside a registered
    # primary tree, or the command explicitly targets one (cd / git -C / --git-dir /
    # --work-tree). FOREMAN_PRIMARY_TREES comes from fworker (router.LANE_REPO +
    # /home/lumen/bin); the fallback mirrors it so a stripped env can't drop the wall.
    # One sanctioned exception: `git merge --ff-only origin/main|master` — the deploy
    # fast-forward every landed PR needs (repo_reconcile.py's own BEHIND_ONLY advice,
    # and the only op in the primary's reflog history). ff-only onto the remote
    # default can never switch branches or introduce unreviewed content; anything
    # else that moves HEAD is denied.
    PRIMARY_TREES=${FOREMAN_PRIMARY_TREES:-/home/lumen/foreman:/home/lumen/bin:/home/lumen/pokemon:/home/lumen/questfree:/home/lumen/pstack:/home/lumen/mission-control}
    # wq#1256 (#19 sync-live guard): git clean -f/--force is just as destructive
    # to a live tree as checkout/reset — it discards untracked files with no
    # undo. The sanctioned path for resyncing a primary is /home/lumen/bin/sync-live
    # <repo> <expected-sha>, which fetches, verifies the SHA landed on
    # origin/main, refuses on a dirty tree, and only then does the reset — so
    # the reason string below points there instead of leaving a worker to guess.
    # wq#929: `git commit` was never in this verb set, so the wall stopped a worker
    # moving a primary's BRANCH but not writing an unreviewed COMMIT onto it. That is
    # how 2719f89 (a dispatcher lane-config change, 2026-08-23 17:01Z) landed: it sat
    # on /home/lumen/foreman's main, diverged the tree from origin, and — since the
    # ff-merge below is the only sanctioned deploy op — stalled every later deploy
    # until it was reconciled. Commit-creating verbs are denied for the same reason
    # branch-moving ones are: content reaches a primary only by fast-forwarding onto
    # origin/<default>, which means only after review.
    #
    # wq#1316: split the command into shell segments FIRST, then run each verb
    # detector against one segment at a time. The old shape was a single
    # whole-string regex that faked segmentation with `[^|;&]*` between `git`
    # and the verb, and that leaked two ways (both reproduced live against the
    # shipped guard, report reports/1309_guard_merge_regex_verify.md):
    #   1. the ff-only carve-out was a whole-string negation, so ONE sanctioned
    #      `git merge --ff-only origin/main && git merge evil` suppressed the
    #      block for the un-sanctioned second command;
    #   2. the terminator set had to be hand-maintained, and wq#1285's
    #      `merge([[:space:]]|$)` omitted every space-free terminator bash
    #      accepts — `git merge;true`, `git merge>foo`, `git merge&`,
    #      `(git merge)`, `bash -c "git merge"` all ran unblocked in a primary.
    # Segmenting makes the carve-out apply only to the segment that earned it,
    # and lets the merge word end at a segment boundary instead of needing `;`
    # `&` `|` `(` `)` spelled into the regex.
    # wq#1335 (ISSUE-LOG #38): path-scoped `git [-C <dir>] checkout -- <relpath>...`
    # and `git [-C <dir>] restore [--staged] <relpath>...` move no branch and no
    # HEAD — they only discard working-tree changes to named files — and lumen-pr's
    # own land-refusal prescribes exactly `git -C <primary> checkout -- <path>` as
    # the sanctioned discard, so the wall was blocking the remedy the adjacent tool
    # recommends. The carve-out is a full-segment anchored match: every path must be
    # relative (no leading `/` or `-`; `.` alone doesn't qualify), the segment may
    # contain no `..` anywhere (also covers the -C target), and no branch-creating/
    # detaching token (-b/-B/--orphan/--detach) — anchoring already precludes flags
    # between the verb and the paths, the token check is belt-and-braces. Anything
    # that doesn't match the exact shape (checkout <ref> -- f, restore --source=...,
    # quoted paths, redirects) falls through to the deny below, per §3.3 of
    # research/WORKER_EXEC_CAPABILITY_GAP.md.
    PATH_SCOPED_GIT_RE='^[[:space:]]*git([[:space:]]+-C[[:space:]]+/[A-Za-z0-9._/-]+)?[[:space:]]+(checkout[[:space:]]+--|restore([[:space:]]+--staged)?)([[:space:]]+\.?[A-Za-z0-9_][A-Za-z0-9._/-]*)+[[:space:]]*$'
    GIT_MOVE=0
    while IFS= read -r seg; do
      [ -n "$seg" ] || continue
      if printf '%s' "$seg" | grep -qE "$PATH_SCOPED_GIT_RE" \
          && ! printf '%s' "$seg" | grep -q '\.\.' \
          && ! printf '%s' "$seg" | grep -qE '(^|[[:space:]])(-b|-B|--orphan|--detach)([[:space:]]|$)'; then
        continue
      fi
      if printf '%s' "$seg" | grep -qE '\bgit\b.*\b(checkout|switch|restore|reset|rebase|pull|commit|cherry-pick|revert|am)\b'; then
        GIT_MOVE=1
      # wq#1285: \bmerge\b alone also matches the read-only plumbing verbs
      # merge-base/merge-tree/merge-file (the word boundary sits on the hyphen),
      # so a diagnostic `git merge-base --is-ancestor` — the exact primitive
      # sync-live and this guard's own #19 spec use to verify a commit landed —
      # was tripping the branch-moving-verb block in a primary tree. `merge` is
      # the branch-moving verb only when the next character ends the word AND is
      # not a hyphen; that excludes the whole merge-* plumbing family without
      # enumerating which characters may follow.
      elif printf '%s' "$seg" | grep -qE '\bgit\b.*\bmerge($|[^-[:alnum:]_])' \
          && ! printf '%s' "$seg" | grep -qE '\bgit\b.*\bmerge +--ff-only +origin/(main|master)($|[^-[:alnum:]_./])'; then
        GIT_MOVE=1
      elif printf '%s' "$seg" | grep -qE '\bgit\b.*\bclean\b.*(-[a-zA-Z]*f[a-zA-Z]*\b|--force\b)'; then
        GIT_MOVE=1
      fi
      if [ "$GIT_MOVE" = 1 ]; then break; fi
    done <<< "$(printf '%s' "$target" | tr '|;&()' '\n\n\n\n\n')"
    if [ "$GIT_MOVE" = 1 ]; then
      HOOK_CWD=$(jfield '.cwd')
      IFS=':' read -ra PT_ARR <<< "$PRIMARY_TREES"
      for ptree in "${PT_ARR[@]}"; do
        [ -n "$ptree" ] || continue
        case "$HOOK_CWD" in
          "$ptree"|"$ptree"/*)
            block "git branch/HEAD-moving or commit-creating verb inside PRIMARY tree $ptree denied (wq#900/wq#929) — primaries stay on their default branch and take new content only by deploy ff-merge; use sync-live to resync a primary, or do git work (and land it with lumen-pr) in your own worktree (deploy ff: only 'git merge --ff-only origin/<default>' is allowed here)" "$RAW_CMD" ;;
        esac
        if printf '%s' "$target" | grep -qE "(cd +|-C +|--git-dir[= ]|--work-tree[= ])[\"']?$ptree(/|['\" ;&|]|\$)"; then
          block "git branch/HEAD-moving or commit-creating verb targeting PRIMARY tree $ptree denied (wq#900/wq#929) — primaries stay on their default branch and take new content only by deploy ff-merge; use sync-live to resync a primary, or do git work (and land it with lumen-pr) in your own worktree (deploy ff: only 'git merge --ff-only origin/<default>' is allowed here)" "$RAW_CMD"
        fi
      done
    fi

    # ---- wq#717 fire-mode diagnosis phase: read-only-diagnosis denylist ----
    # fworker sets FOREMAN_FIRE_PHASE=diagnosis only for a fleet-fires ticket's
    # first-phase child (harness/fire_gate.py / FIRE_MODE.md). Defense-in-depth on
    # top of the prompt instruction "diagnosis only, no production changes, write
    # the plan and stop" — not a full read-only sandbox (this is a denylist, same
    # style as the house rules above, not an allowlist), so it can still miss a
    # command shaped unlike anything below. The two hard guarantees for this phase
    # are: this denylist, and the Write/Edit plan-path restriction further down.
    if [ "${FOREMAN_FIRE_PHASE:-}" = "diagnosis" ]; then
      if printf '%s' "$target" | grep -qE '\bsystemctl\b +(start|restart|reload|enable|kill|edit)\b'; then
        block "fire-mode diagnosis: systemctl mutation denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\b(kill|pkill|killall)\b'; then
        block "fire-mode diagnosis: kill/pkill/killall denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\bservice\b +[^ ]+ +(start|restart|stop|reload)\b'; then
        block "fire-mode diagnosis: service start/restart/stop/reload denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\b(apt|apt-get|dnf|yum|npm|pip|pip3|gem)\b +.*\b(install|remove|uninstall)\b'; then
        block "fire-mode diagnosis: package install/remove denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\b(mv|chmod|chown|useradd|usermod|crontab|touch|tee)\b'; then
        block "fire-mode diagnosis: filesystem mutation denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\bsed\b[^|;]*-i\b'; then
        block "fire-mode diagnosis: in-place sed denied (wq#717)" "$RAW_CMD"
      fi
      if printf '%s' "$target" | grep -qE '\b(curl|wget)\b[^|;]*(-X *(POST|PUT|PATCH|DELETE)\b|--data|(^|[^-])-d\b)'; then
        block "fire-mode diagnosis: mutating HTTP call denied (wq#717)" "$RAW_CMD"
      fi
    fi
  done

  # The Bash output cap does NOT live here. wq#729 capped by rewriting the command
  # to `{ cmd } 2>&1 | head -c 2000`; wq#951 proved claude-code 2.1.233 honours
  # BASH_MAX_OUTPUT_LENGTH natively, so wq#954 moved the cap to that variable in
  # fworker.build_env(). Do not reintroduce a pipeline cap: piping subshells the
  # left side, so `cd`/`export`/`source` had to be exempted from the rewrite, and
  # those exemptions were the entire residual leak (168 results, 725 KB, 20.1% of
  # the corpus's over-cap bytes — research/UNCAPPED_BASH.md §4). A native cap
  # truncates the assembled result instead of the pipeline, so no command shape
  # can opt out and nothing can swallow a shell-state change.
  ;;
Read|Edit|Write|Grep|Glob)
  FILE_PATH=$(jfield '.tool_input.file_path')
  # Grep/Glob use "path", not "file_path" — check both so the same credential deny
  # list applies to a Grep search over a denied path, not just a direct Read of it
  # (wq#717: closes the gap a fire-mode worker could otherwise use to exfiltrate
  # FIRE_TOKEN_FILE's content via a search hit instead of a Read).
  GREP_PATH=$(jfield '.tool_input.path')
  for CHECK_PATH in "$FILE_PATH" "$GREP_PATH"; do
    [ -n "$CHECK_PATH" ] || continue
    case "$CHECK_PATH" in
      *.env|*.credentials.json|*claude_credentials.md|*claude_credentials.md.*|/home/lumen/minerva/context/sections/*credentials*|/home/lumen/minerva/context/sections.json|/home/lumen/minerva/context/sections.json.*|/home/lumen/agent/.env|/home/lumen/agent/.env/*|*/.claude/*|/home/lumen/.claude/*|/home/lumen/.secrets|/home/lumen/.secrets/*)
        block "denied path: $CHECK_PATH" "$CHECK_PATH" ;;
    esac
  done

  # ---- wq#717 fire-mode diagnosis phase: Write/Edit confined to the plan doc ----
  # This is the one hard (not denylist-best-effort) guarantee of "read-only
  # diagnosis phase ONLY": FOREMAN_FIRE_PLAN_PATH is the single path fworker
  # permits a diagnosis-phase child to Write/Edit (its own plan doc, harness/
  # fire_gate.py's plan_path()) — everything else is denied outright.
  if [ "${FOREMAN_FIRE_PHASE:-}" = "diagnosis" ] && { [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ]; }; then
    if [ -z "${FOREMAN_FIRE_PLAN_PATH:-}" ] || [ "$FILE_PATH" != "$FOREMAN_FIRE_PLAN_PATH" ]; then
      block "fire-mode diagnosis phase is read-only except the plan doc (wq#717): $FILE_PATH" "$FILE_PATH"
    fi
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
    # wq#833: the key has to be size + *nanosecond* mtime, not `stat -c %Y`.
    # Whole-second mtime cannot tell an Edit-then-Read-to-verify apart from a
    # genuine repeat read — in a fast agent loop both land in the same wall-clock
    # second, and the agent was then told its stale copy was still current.
    # `%.9Y` keeps ~100ns resolution; a stat too old to know the precision spec
    # emits it literally, which degrades to the old %Y behaviour rather than
    # freezing the key.
    SESSION_ID=$(jfield '.session_id')
    MTIME=$(stat -c '%s:%Y:%.9Y' "$FILE_PATH" 2>/dev/null)
    if [ -n "$SESSION_ID" ] && [ -n "$MTIME" ]; then
      CACHE_DIR=${FOREMAN_READ_CACHE_DIR:-$EVENTS_DIR/read_cache}
      mkdir -p "$CACHE_DIR" 2>/dev/null
      CACHE_FILE="$CACHE_DIR/$SESSION_ID.jsonl"
      if [ -f "$CACHE_FILE" ]; then
        HIT=$(jq -sr --arg p "$FILE_PATH" --arg o "$EFF_OFFSET" --arg l "$EFF_LIMIT" --arg m "$MTIME" \
          'map(select(.path == $p and .offset == $o and .limit == $l and .fp == $m)) | length' \
          "$CACHE_FILE" 2>/dev/null)
        if [ -n "$HIT" ] && [ "$HIT" != "0" ]; then
          block "already read this session, file unchanged since (wq#730 R5): $FILE_PATH offset=$EFF_OFFSET limit=$EFF_LIMIT — reuse the earlier tool result instead of re-reading" "$FILE_PATH"
        fi
      fi
      jq -nc --arg p "$FILE_PATH" --arg o "$EFF_OFFSET" --arg l "$EFF_LIMIT" --arg m "$MTIME" \
        '{path:$p, offset:$o, limit:$l, fp:$m}' >> "$CACHE_FILE" 2>/dev/null
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
