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

# wq#2277 (TOKEN_ENGINE_DESIGN.md §3.4): at SessionEnd, ask the token-engine
# daemon to revoke every token it minted for this ticket. Strictly best-effort —
# a dead broker, absent grant, or timeout must never block session end, because
# the daemon-start reaper re-runs the same sweep for exactly those cases.
#
# wq#2349: the grant is passed EXPLICITLY, from the pair fworker's
# write_ticket_end_grant publishes per attempt. Passing none let te_client default
# to FOREMAN_GRANT_FILE/_SHA — the exec grant, which fworker sets only for a ticket
# with needs:ssh/copy/restart tags — so the broker denied grant:grant_missing_env
# on 283 of 283 calls and §3.4 revocation never ran once. No fallback to that grant
# either: it carries ssh/copy/restart kinds, never {token_engine, daemon}, so it
# would only trade grant_missing_env for grant_no_match. Unset means SKIP rather
# than call-and-be-denied: one quiet denial per session is what hid this.
revoke_minted_tokens() {
  local sock="${TOKEN_ENGINE_SOCK:-/run/token-engine/broker.sock}"
  local te_client="$(dirname "$0")/../token-engine/te_client.py"
  local grant_file="${FOREMAN_TICKET_END_GRANT_FILE:-}"
  local grant_sha="${FOREMAN_TICKET_END_GRANT_SHA:-}"
  [ -n "${FOREMAN_TICKET_ID:-}" ] || { log_event "ticket_end_skip_no_ticket"; return 0; }
  [ -S "$sock" ] || { log_event "ticket_end_skip_no_broker"; return 0; }
  [ -f "$te_client" ] || { log_event "ticket_end_skip_no_client"; return 0; }
  [ -n "$grant_file" ] && [ -n "$grant_sha" ] || { log_event "ticket_end_skip_no_grant"; return 0; }
  if timeout 25 python3 "$te_client" --verb ticket-end \
       --ticket "$FOREMAN_TICKET_ID" --worker "$WORKER_ID" \
       --grant-file "$grant_file" --grant-sha "$grant_sha" >/dev/null 2>&1; then
    log_event "ticket_end_revoked"
  else
    log_event "ticket_end_revoke_failed"
  fi
  return 0
}

# ---- lifecycle mode: --lifecycle start|stop|end ----
if [ "${1:-}" = "--lifecycle" ]; then
  LIFECYCLE=${2:-}
  case "$LIFECYCLE" in
    end) log_event "$LIFECYCLE"; revoke_minted_tokens; exit 0 ;;
    start|stop) log_event "$LIFECYCLE"; exit 0 ;;
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
    # wq#2514: Bash twin of the git credential-store deny in the Read/Grep branch.
    if printf '%s' "$target" | grep -qE '\.git-credentials|(^|[/[:space:]])\.netrc\b|\.fleet_pw\b'; then
      block "denied path: git credential store (.git-credentials*, .netrc, .fleet_pw)" "$RAW_CMD"
    fi
    # wq#2546: the Read/Grep deny below already covers these two paths for the
    # Read/Grep/Glob tools; this is the Bash-side twin — same class as the
    # .secrets/.git-credentials denies just above, because `cat`/`tail`/`grep`
    # over a raw path is invisible to the file-path checks those tools carry.
    # Both stores hold values masking.py never touches (they are written by the
    # `claude` binary and by supergateway, not by anything in this repo) and
    # wq#2514's consumer scan found the live RealLumenHere PAT in both.
    if printf '%s' "$target" | grep -qE "$HOME/\.claude/projects\b|/home/[a-z]+/\.claude/projects\b"; then
      block "denied path: CC session transcripts (~/.claude/projects — unmasked model-context store, wq#2546)" "$RAW_CMD"
    fi
    if printf '%s' "$target" | grep -qE '/home/lumen/logs/supergateway\b'; then
      block "denied path: supergateway gateway log (unmasked tool-call bodies, wq#2546)" "$RAW_CMD"
    fi
    # wq#2980: Bash twin of the agent/.env and litellm_config.yaml.* Read/Grep denies.
    # wq#2975 a2 ran `python3 -c` over agent/.env and the config backups, printing the
    # live Langfuse secret whole (6cf8c0a3e6a1) and as an 8...6 "masked" slice
    # (b21310999254) — a slice is still 14 chars of the key. The .env backups and the
    # litellm_config.yaml.* copies carry the same value. The plain litellm_config.yaml
    # stays open to Bash for a narrow `grep -n model_name`.
    if printf '%s' "$target" | grep -qE '(/home/lumen|~|\$HOME|\$\{HOME\})/agent/\.env\b|litellm_config\.yaml\.[[:alnum:]_]'; then
      block "denied path: agent/.env* or a litellm_config.yaml.* copy (live Langfuse secret inside, wq#2980)" "$RAW_CMD"
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

    # ---- wq#2274: credential-EMITTING command shapes ----
    # The path denies above stop READING credential files; nothing stopped the
    # commands whose normal output prints a live token into a transcript. wq#2243
    # burned a GitHub PAT exactly this way: a remote listing printed a clone's
    # remote URL, token embedded, into an orchestrator transcript (ISSUE-LOG #537).
    # DENY, never rewrite: the pipeline-rewrite lever was retired by wq#954 (see
    # the cap note below) — piping subshells the left side, and the cd/export/
    # source exemptions that required were the entire residual leak.
    # Sanctioned shape (instinct 4, mask-before-print): the same statement piped
    # through /home/lumen/bin/mask-secrets is allowed — it redacts secret values
    # before they reach the transcript. Statements are split on ;/&/newline but
    # NOT on |, so the pipe relationship to mask-secrets survives the split and
    # an unmasked sibling statement in the same command line still blocks.
    MASKED_RE='\|[[:space:]]*(/home/lumen/bin/)?mask-secrets($|[^-[:alnum:]_])'
    while IFS= read -r stmt; do
      [ -n "$stmt" ] || continue
      if printf '%s' "$stmt" | grep -qE "$MASKED_RE"; then continue; fi
      if printf '%s' "$stmt" | grep -qE '\bgit\b[^|]*\bremote\b[^|]*((^| )-v\b|--verbose\b|(^| )get-url\b)'; then
        block "credential-emitting shape: git remote -v/--verbose/get-url denied (wq#2274 — a remote URL can embed a token; wq#2243 leaked a PAT exactly this way). Pipe through /home/lumen/bin/mask-secrets or report path-only" "$RAW_CMD"
      fi
      if printf '%s' "$stmt" | grep -qE '\bgit\b[^|]*\bconfig\b[^|]*((^| )-l\b|--list\b|--get-urlmatch\b)'; then
        block "credential-emitting shape: git config -l/--list/--get-urlmatch denied (wq#2274 — config dumps can embed credential.helper values and tokenized URLs). Pipe through /home/lumen/bin/mask-secrets" "$RAW_CMD"
      fi
      # env is a dump only when nothing follows it (or only a pipe/redirect does):
      # `env VAR=x cmd` runs a command with one var set and emits nothing — allowed.
      if printf '%s' "$stmt" | grep -qE '(^|[^[:alnum:]_./-])env[[:space:]]*($|[|>])'; then
        block "credential-emitting shape: env dump denied (wq#2274 — the environment holds live tokens). Pipe through /home/lumen/bin/mask-secrets; env VAR=x cmd stays allowed" "$RAW_CMD"
      fi
      if printf '%s' "$stmt" | grep -qE '(^|[^[:alnum:]_./-])printenv($|[^-[:alnum:]_])'; then
        block "credential-emitting shape: printenv denied (wq#2274 — prints raw env values, name-free so masking can miss them). Pipe through /home/lumen/bin/mask-secrets" "$RAW_CMD"
      fi
    done <<< "$(printf '%s' "$target" | tr ';&' '\n\n')"
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

    # ---- wq#2856: never overwrite a live bin script in place ----
    # `cp new /home/lumen/bin/x` (or `> /home/lumen/bin/x`) rewrites x's existing
    # inode; a timer's bash still running x resumes on the NEW bytes at the OLD
    # offset (lumen-re-sync.service, 2026-09-28T03:25:42Z: "LOCK: unbound
    # variable"). Only an overwrite of an EXISTING regular file is denied — a
    # fresh file, a .bak copy, or cp to x.tmp then mv all stay allowed.
    BIN_DIR=${FOREMAN_GUARD_BIN_DIR:-/home/lumen/bin}
    while IFS= read -r stmt; do
      [ -n "$stmt" ] || continue
      DSTS=()
      if printf '%s' "$stmt" | grep -qE '(^|[^[:alnum:]_./-])cp[[:space:]]'; then
        read -ra W <<< "$(printf '%s' "$stmt" | sed -E 's/^.*(^|[^[:alnum:]_./-])cp[[:space:]]+//' | tr -d "\"'()")"
        OPERANDS=(); TDIR=
        for ((i = 0; i < ${#W[@]}; i++)); do
          case "${W[i]}" in
            -t|--target-directory) TDIR=${W[i+1]:-}; i=$((i + 1)) ;;
            --target-directory=*) TDIR=${W[i]#*=} ;;
            -*) ;;
            *) OPERANDS+=("${W[i]}") ;;
          esac
        done
        if [ -n "$TDIR" ]; then
          for s in "${OPERANDS[@]}"; do DSTS+=("$TDIR/$(basename "$s")"); done
        elif [ ${#OPERANDS[@]} -ge 2 ]; then
          d=${OPERANDS[-1]}
          if [ -d "$d" ]; then
            for s in "${OPERANDS[@]:0:${#OPERANDS[@]}-1}"; do DSTS+=("$d/$(basename "$s")"); done
          else
            DSTS+=("$d")
          fi
        fi
      fi
      # `>` truncates the same inode too; `>>` and `2>` are left alone.
      while IFS= read -r r; do
        [ -n "$r" ] && DSTS+=("$r")
      done <<< "$(printf '%s' "$stmt" | grep -oE '(^|[^>0-9&])>[[:space:]]*[^[:space:]>|&]+' | sed -E 's/^[^>]*>[[:space:]]*//' | tr -d "\"'")"
      for d in "${DSTS[@]}"; do
        d=${d/#\~/$HOME}; d=${d/#\$HOME/$HOME}; d=${d/#\$\{HOME\}/$HOME}
        [ -L "$d" ] && d=$(readlink -f "$d")
        case "$d" in "$BIN_DIR"/*) ;; *) continue ;; esac
        if [ -f "$d" ]; then
          block "in-place overwrite of live script $d denied (wq#2856 — a running timer/unit keeps reading the same inode and executes the new bytes at the old offset). Install by rename: /home/lumen/bin/install-script <src> $d (repo copy: /home/lumen/foreman/ops/wq2856-atomic-install/install-script), or cp to $d.tmp then mv -f" "$RAW_CMD"
        fi
      done
    done <<< "$(printf '%s' "$target" | tr ';&|' '\n\n\n')"

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
    # wq#1752 (ISSUE-LOG #302): kb and the shallow nest archive are default add_dirs
    # (agents.yaml) too but were missing from this fallback and from
    # primary_tree_guard.STATIC_PRIMARY_TREES, so a stripped env dropped the wall
    # for exactly those two trees — added to both.
    # wq#1761 (ISSUE-LOG #313): same gap, third default add_dir — work-queue (the
    # sqlite store + wq CLI backing the fleet task queue) — added to both here too.
    # wq#1540: minerva (standalone repo, no LANE_REPO entry) likewise, in both.
    # wq#2577: /home/lumen/agent and /home/lumen/logs are now add_dirs for the
    # audit/research-lane archetypes (agents.yaml rca-investigation,
    # research-synthesis) so a fleet audit can read unit logs and agent state —
    # same gap class, added to both.
    PRIMARY_TREES=${FOREMAN_PRIMARY_TREES:-/home/lumen/foreman:/home/lumen/bin:/home/lumen/pokemon:/home/lumen/questfree:/home/lumen/pstack:/home/lumen/mission-control:/home/lumen/kb:/home/lumen/projects/nest:/home/lumen/agents/work-queue:/home/lumen/minerva:/home/lumen/agent:/home/lumen/logs}
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
    # wq#2504 (ISSUE-LOG #663, root cause of wq#2477): `git stash` was missing from
    # this wall. It is a hard reset underneath, and with -u/-a also a clean of
    # untracked files — the wq#2081 land step ran `git stash -u` in the live
    # foreman tree to get past lumen-pr land's dirty-tree refusal, swept 73
    # untracked files into stash@{0} and never popped it. Every stash form is
    # denied in a primary (bare, push, save, -u/-a/-k, apply/pop/drop/clear/branch/
    # store) except the read-only list/show and `create`, which builds a stash
    # commit without touching the tree or the stash ref. The stash token must be a
    # whole word, so `git show stash@{0}` (inspecting a backup) stays allowed.
    GIT_STASH_RE='\bgit\b.*[[:space:]]stash($|[[:space:]<>`"'"'"'])'
    GIT_STASH_RO_RE='\bgit\b.*[[:space:]]stash[[:space:]]+(list|show|create)($|[[:space:]<>`"'"'"'])'
    GIT_MOVE=0
    GIT_STASH=0
    while IFS= read -r seg; do
      [ -n "$seg" ] || continue
      if printf '%s' "$seg" | grep -qE "$PATH_SCOPED_GIT_RE" \
          && ! printf '%s' "$seg" | grep -q '\.\.' \
          && ! printf '%s' "$seg" | grep -qE '(^|[[:space:]])(-b|-B|--orphan|--detach)([[:space:]]|$)'; then
        continue
      fi
      # One stash token per read-only segment: a second one (e.g. smuggled in
      # backticks, which the segment split does not cut on) is judged on its own.
      if printf '%s' "$seg" | grep -qE "$GIT_STASH_RE" \
          && { ! printf '%s' "$seg" | grep -qE "$GIT_STASH_RO_RE" \
               || [ "$(printf '%s' "$seg" | grep -oE '[[:space:]`]stash($|[[:space:]<>`"'"'"'])' | grep -c ^)" -gt 1 ]; }; then
        GIT_STASH=1
        break
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
    if [ "$GIT_MOVE" = 1 ] || [ "$GIT_STASH" = 1 ]; then
      if [ "$GIT_STASH" = 1 ]; then
        WALL_WHAT="git stash"
        WALL_WHY="(wq#2504) — stash is a hard reset underneath and with -u/-a a clean of untracked files; wq#2477 lost 73 untracked files to one. If lumen-pr land or a merge is refused by untracked files, move or remove ONLY the specific untracked paths the merge adds (lumen-pr land prints them: rm -- <primary>/<path>), never the whole tree; read-only git stash list/show stay allowed"
      else
        WALL_WHAT="git branch/HEAD-moving or commit-creating verb"
        WALL_WHY="(wq#900/wq#929) — primaries stay on their default branch and take new content only by deploy ff-merge; use sync-live to resync a primary, or do git work (and land it with lumen-pr) in your own worktree (deploy ff: only 'git merge --ff-only origin/<default>' is allowed here)"
      fi
      HOOK_CWD=$(jfield '.cwd')
      IFS=':' read -ra PT_ARR <<< "$PRIMARY_TREES"
      for ptree in "${PT_ARR[@]}"; do
        [ -n "$ptree" ] || continue
        case "$HOOK_CWD" in
          "$ptree"|"$ptree"/*)
            block "$WALL_WHAT inside PRIMARY tree $ptree denied $WALL_WHY" "$RAW_CMD" ;;
        esac
        if printf '%s' "$target" | grep -qE "(cd +|-C +|--git-dir[= ]|--work-tree[= ])[\"']?$ptree(/|['\" ;&|]|\$)"; then
          block "$WALL_WHAT targeting PRIMARY tree $ptree denied $WALL_WHY" "$RAW_CMD"
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
      *.env|*.credentials.json|*claude_credentials.md|*claude_credentials.md.*|/home/lumen/minerva/context/sections/*credentials*|/home/lumen/minerva/context/sections.json|/home/lumen/minerva/context/sections.json.*|/home/lumen/agent/.env|/home/lumen/agent/.env/*|/home/lumen/agent/.env.*|*/.claude/*|/home/lumen/.claude/*|/home/lumen/.secrets|/home/lumen/.secrets/*|/var/lib/token-engine|/var/lib/token-engine/*|/home/lumen/logs/supergateway|/home/lumen/logs/supergateway/*)
        block "denied path: $CHECK_PATH" "$CHECK_PATH" ;;
      # wq#2514: git's plaintext credential store and its .bak copies. Same set
      # capability_lint.DIR_DENY_BASENAME_GLOBS_HOME hard-denies as a grant; without
      # it here a Read by absolute path went through and burned every token in it.
      */.git-credentials|*/.git-credentials.*|*/.netrc|*/.fleet_pw|*.pw)
        block "denied path: $CHECK_PATH" "$CHECK_PATH" ;;
      # wq#2951: the LiteLLM config carries the live Langfuse secret as a literal under
      # environment_variables (ISSUE-LOG 904/914), and a whole-file Read/Grep dumped it
      # into wq#2904's and wq#2914's context, burning 6cf8c0a3e6a1 twice. Bash stays
      # open so a narrow `grep -n model_name` still works. Lift this once the literal
      # is an env reference (wq#2916's rotation runbook).
      /home/lumen/agent/litellm_config.yaml|/home/lumen/agent/litellm_config.yaml.*)
        block "denied path: $CHECK_PATH (literal Langfuse secret inside, wq#2951; grep the lines you need via Bash)" "$CHECK_PATH" ;;
      # wq#3678: the LibreChat compose file inlines the Mongo root pw (also inside the
      # mongodb:// URI), JWT_SECRET and JWT_REFRESH_SECRET; wq#3090 and wq#3665 each
      # Read it whole and burned all three (6bc6f59051e9, 19dc5bec2297, 7d555026ff55).
      # Lift once those move to a 0600 env_file (wq#3093's rotation).
      /home/lumen/docker/docker-compose.yml|/home/lumen/docker/docker-compose.yml.*)
        block "denied path: $CHECK_PATH (inline LibreChat Mongo/JWT secrets, wq#3678)" "$CHECK_PATH" ;;
    esac
  done

  # ---- wq#1719: PRIMARY trees are write-frozen for workers ----
  # agents.yaml's `defaults.add_dirs` grants every worker --add-dir reach into
  # the fleet's primary repo roots (foreman/bin/pokemon/mailbot/kb/nest — wq#1245,
  # read access for cross-repo verification), but Edit/Write is an unscoped
  # tool-level allow (worker-settings.json), so that same add_dir also lets a
  # worker edit files in place inside a primary tree. Commits there are denied
  # (wq#900 above), so a worker that edits in place and then dies leaves code
  # that is live, unreviewed, and unlandable — fworker-wq1676-a3 (run
  # 08:34-09:04Z, 2026-09-10) edited 5 foreman harness files 08:43-08:51Z and
  # bin/wq at 08:56Z this way, then PERMISSION_LOOP'd trying to diff them back
  # into its own worktree (_runs/wq1676/failure.json). Mirrors the Bash
  # PRIMARY_TREES wall above: any Write/Edit whose target resolves inside a
  # registered primary tree is denied outright, regardless of add_dirs. A
  # worker's own worktree is never itself a primary tree (worktrees live under
  # /home/lumen/worktrees/), so this cannot collide with a legitimate edit.
  # wq#717's own plan doc (FOREMAN_FIRE_PLAN_PATH, harness/fire_gate.py plan_path())
  # is the one sanctioned exception: it is untracked orchestrator state living under
  # a primary tree's plans/ dir by design, never landed via git/lumen-pr, and the
  # narrower fire-mode check just below already confines diagnosis-phase Write/Edit
  # to exactly this one path.
  if [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ]; then
    if [ -z "${FOREMAN_FIRE_PLAN_PATH:-}" ] || [ "$FILE_PATH" != "$FOREMAN_FIRE_PLAN_PATH" ]; then
      # wq#1752 (ISSUE-LOG #302): kb + shallow nest archive added, same gap as the
      # Bash PRIMARY_TREES wall above.
      # wq#1761 (ISSUE-LOG #313): work-queue added, same gap, third default add_dir.
      # wq#2577: /home/lumen/agent + /home/lumen/logs added, same gap — now
      # add_dirs for the audit/research-lane archetypes (agents.yaml
      # rca-investigation, research-synthesis).
      WRITE_PRIMARY_TREES=${FOREMAN_PRIMARY_TREES:-/home/lumen/foreman:/home/lumen/bin:/home/lumen/pokemon:/home/lumen/questfree:/home/lumen/pstack:/home/lumen/mission-control:/home/lumen/kb:/home/lumen/projects/nest:/home/lumen/agents/work-queue:/home/lumen/minerva:/home/lumen/agent:/home/lumen/logs}
      # wq#1831: kb/ISSUE-LOG.md is the one sanctioned Edit target inside a primary
      # tree. The wall's remedy ("land it with lumen-pr") is unreachable for kb — the
      # repo has no remote and no PR flow, and a worker's filesystem bound cannot
      # reach a kb worktree anyway (wq#1744's block) — so since wq#1719 every worker
      # with a log row has appended it with a shell redirect instead (row 349 records
      # the practice; rows 342-478 landed that way), which the doctrine REQUIRES of
      # every worker that hits an issue. This carve-out makes the required act
      # expressible with the intended tool. Edit only: appends and status flips
      # (OPEN->FIXED, row 349's complaint) are the log's two write shapes, and both
      # are Edits; Write stays blocked because whole-file replacement of the fleet's
      # institutional memory is never a legitimate worker op. Exact-string match on
      # the raw tool_input path, mirroring the wall it punches through — an
      # unnormalized spelling ("kb/../kb/ISSUE-LOG.md") misses the carve-out and
      # falls through to the block, i.e. fails closed.
      if [ "$TOOL_NAME" = "Edit" ] && [ "$FILE_PATH" = "/home/lumen/kb/ISSUE-LOG.md" ]; then
        :
      else
      IFS=':' read -ra WPT_ARR <<< "$WRITE_PRIMARY_TREES"
      for ptree in "${WPT_ARR[@]}"; do
        [ -n "$ptree" ] || continue
        case "$FILE_PATH" in
          "$ptree"|"$ptree"/*)
            block "Write/Edit inside PRIMARY tree $ptree denied (wq#1719/wq#900) — primary repo roots are read-only for workers; the one exception is Edit on /home/lumen/kb/ISSUE-LOG.md (wq#1831); for anything else make the change in your own worktree${WORKTREE_ROOT:+ ($WORKTREE_ROOT)} and land it with lumen-pr" "$FILE_PATH" ;;
        esac
      done
      fi
    fi
  fi

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
