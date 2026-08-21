# T2a — Worker Harness Design (wq#614)

Author: fbuild-614 (fable-5@high), 2026-08-15. Spec: BUILD_PLAN.md §T2a.
Refs: FOREMAN_RESEARCH.md §4/§5/§12/§13, CLAUDE_CLI_INTERNALS.md §A/§E/§F, PROBE_RESULTS.md.
Status: DESIGN FOR REVIEW. Frank reads this before the sonnet-5@high build phase starts.
Scope: ONE cloud worker at a time on the shared login. Multi-worker auth is T2b/T2b-gate.

## 0. TL;DR

Build a bespoke harness: one supervisor process per ticket (`fworker`), launched detached
via lumen-run, that spawns exactly one `claude -p` child with a per-ticket git worktree,
a scrubbed env, the acceptEdits+deny+guard-hook settings pack, and parses the child's
stream-json stdout for supervision and LIMIT-vs-CRASH classification.

The native `claude --bg` / `claude agents` surface was spiked (§1) and REJECTED as the
supervision substrate. `claude agents --json` is kept as a read-only observability aid.

## 1. Spike: native `--bg` / `claude agents` substrate (30 min, one no-op)

Ran 2026-08-15 on claude 2.1.220, scratch cwd, marker env + hook-instrumented settings,
one sonnet-5 no-op dispatch. Raw artifacts: /tmp/fbuild-614-1539-a34c76/spike-bg/.

What works:
- Per-worker env injection SURVIVES: a `FOREMAN_MARKER` env var set at dispatch was visible
  to the session's hooks. `CLAUDE_CODE_EFFORT_LEVEL=low` pinned the session effort (status
  line showed `low`, Stop-hook `$CLAUDE_EFFORT` = `low`).
- `--settings <file>` injection SURVIVES: our SessionStart/Stop hooks fired.
- `claude agents --json` is scriptable without a TTY: pid, cwd, kind, sessionId, name,
  status (`idle`), state (`done`). `--all` includes completed sessions. `claude stop <id>`
  works headless.

Why it still loses as the substrate:
- `--bg` REFUSES `-p` ("--print never starts the interactive session that claude agents
  attaches to"). A background agent is a full interactive TUI session, so:
  - No stream-json stdout. The whole supervision plan (init/session_id capture, subtype
    result frames, rate_limit_event/api_error frames, the §E LIMIT-vs-CRASH classifier)
    has no input channel.
  - `claude logs <id>` returns raw ANSI screen scrapes, not events.
  - No result subtype at all: the session finishes the prompt and sits IDLE forever
    (observed `status:idle state:done`, process resident) instead of exiting. Completion
    detection would be polling a TUI state field; cleanup needs an explicit `claude stop`.
  - Dispatched sessions default to MANUAL permission mode (observed `permission-mode:
    default` in the transcript, "manual mode on" in the TUI). Unattended, one permission
    prompt hangs the ticket silently. Fixable per-dispatch, but it is the wrong default
    for a fleet.
- The "Starting background service..." daemon adds a moving part we do not control and
  gain nothing from.

VERDICT: bespoke process tracking. Keep `claude agents --json` as a zero-cost extra
signal for restart reconciliation (§8) and fleet visibility; never as the control channel.
Whether plain `-p` children appear in `agents --json` is unconfirmed; the design does not
depend on it (1-line check during build, note the answer in the README).

## 2. Components and file layout

    /home/lumen/foreman/
      harness/
        fworker.py              supervisor: spawn + parse + classify + wq lifecycle
        worker-settings.json    settings pack (§4) — replaces worker-settings-interim.json
        guard.sh                PreToolUse fail-closed guard (§5)
        prompts/worker.md       worker prompt template (identity, one-ticket contract)
        tests/                  unit tests: classifier, guard, settings lint
      events/<worker-id>.jsonl  hook-emitted lifecycle events (§6)
      _runs/wq<ID>/             per-ticket run state: attempt-N.json, prior-attempt-notes.md
      state.json, STOP          governor-owned (T1)
    /home/lumen/worktrees/wq<ID>-a<N>/   per-attempt git worktrees (per target repo)

One fworker per ticket. The orchestrator (today: ORCH session; later T3) runs:

    lumen-run start --name fworker-wq<ID> -- \
      python3 /home/lumen/foreman/harness/fworker.py run \
        --ticket <ID> --repo <path> --model <id> --effort <name> \
        --archetype <name> [--resume]

lumen-run keeps fworker itself immune to DC-gateway reaping. fworker spawns `claude -p`
as a direct child (pipe on stdout) so the stream is never buffered through a file.

## 3. Worker invocation

Per-attempt setup by fworker, in order:
1. Governor gate: `/home/lumen/foreman/STOP` absent AND
   `foreman_usaged.py can-dispatch <model>` exit 0, else do not spawn (§9).
2. `wq claim --id <ID>` under the worker's registered WQ_AGENT. Claim failure = exit
   CLAIM_ERROR, nothing spawned.
3. Worktree: `git -C <repo> worktree add /home/lumen/worktrees/wq<ID>-a<N>
   -b <type>/wq<ID>-<slug> origin/<default>` (fresh per attempt; prior attempt's worktree
   kept for forensics until the ticket closes).
4. Spawn:

    env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL \
        HOME=/home/lumen \
        FOREMAN_WORKER=<worker-id> WQ_AGENT=<worker-id> \
        CLAUDE_CODE_EFFORT_LEVEL=<effort> \
        CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 \
        CLAUDE_CODE_ENABLE_TELEMETRY=1 <OTEL vars §10> \
      claude -p "<prompt>" \
        --model <full-model-id> --effort <effort> \
        --max-turns <per-archetype> \
        --settings /home/lumen/foreman/harness/worker-settings.json \
        --permission-mode acceptEdits \
        --add-dir /home/lumen/agents/work-queue \
        --output-format stream-json --verbose --include-hook-events \
        </dev/null

Notes, each load-bearing:
- `env -u` scrub: cloud workers must never see the DEFUSED ANTHROPIC_API_KEY or a stale
  ANTHROPIC_BASE_URL (OAuth-vs-gateway precedence footgun, RESEARCH §9).
- Effort is set BOTH ways deliberately: `CLAUDE_CODE_EFFORT_LEVEL` is the hard pin
  (highest precedence, INTERNALS §A) and explicit `--effort` clears the fable/opus-4.x
  launch-effort pin (yW()). Names only, never integers. haiku-4-5: omit both.
- `--max-turns` >= 2 always (P7: tool-call on turn 1 burns the whole budget at 1);
  per-archetype defaults: mechanical-edit 15, probe-build 25, standard-engineering 40,
  rca 60. Wall-clock cap enforced by fworker (default 3600s, rca 7200s): SIGTERM, 30s
  grace, SIGKILL. Not `timeout(1)` — fworker must run the reap path (§7) after the kill.
- stdin from /dev/null (P7 harness note).
- Auth: T2a runs on the live shared login, ONE cloud worker at a time (fworker refuses to
  start if another fworker-wq* lumen-run unit is RUNNING). CLAUDE_CODE_OAUTH_TOKEN comes
  with T2b after the human-gated setup-token.
- The prompt template carries the same one-ticket contract as gh-lane-worker (identity,
  boot, single ticket, lumen-pr land, board line, sentinel last line). Sentinels stay as
  belt-and-braces; the result frame is the real signal.

## 4. Settings pack — worker-settings.json

Mode acceptEdits. NO bypassPermissions anywhere, ever (BUILD_PLAN hard rule). The
`ask` tier is not used: in -p an ask blocks forever and auto-denies; anything ask-worthy
is deny + wq block for Frank (RESEARCH §4). Supersedes worker-settings-interim.json
(interim file had deny rules only, no Read denies, no hooks; delete it at build time).

    {
      "permissions": {
        "defaultMode": "acceptEdits",
        "allow": [
          "Read", "Grep", "Glob", "Edit", "Write",
          "Bash(git *)", "Bash(python3 *)", "Bash(pytest *)", "Bash(node *)",
          "Bash(npm test*)", "Bash(sqlite3 *)",
          "Bash(/home/lumen/bin/wq *)", "Bash(/home/lumen/bin/lumen-pr *)",
          "Bash(/home/lumen/bin/slock *)", "Bash(/home/lumen/bin/lumen-run *)",
          "Bash(docker ps*)", "Bash(docker logs*)",
          "Bash(curl *)"
        ],
        "deny": [
          "Bash(*reboot*)", "Bash(shutdown*)", "Bash(poweroff*)", "Bash(halt*)",
          "Bash(systemctl stop*)", "Bash(systemctl disable*)", "Bash(systemctl mask*)",
          "Bash(dd *)",
          "Bash(*--remove-orphans*)",
          "Bash(ip link set*)", "Bash(macchanger*)",
          "Bash(git push --force*)", "Bash(git push -f*)",
          "Bash(rm -rf /*)",
          "Bash(docker *)",
          "Bash(sudo *)",
          "Bash(claude setup-token*)",
          "Read(**/.env)", "Read(**/.credentials.json)",
          "Read(**/claude_credentials.md)", "Read(/home/lumen/agent/.env)"
        ]
      },
      "hooks": {
        "PreToolUse": [
          { "matcher": "Bash|Read|Edit|Write",
            "hooks": [{ "type": "command",
                        "command": "/home/lumen/foreman/harness/guard.sh" }] }
        ],
        "SessionStart": [ { "hooks": [{ "type": "command",
            "command": "/home/lumen/foreman/harness/guard.sh --lifecycle start" }] } ],
        "Stop":         [ { "hooks": [{ "type": "command",
            "command": "/home/lumen/foreman/harness/guard.sh --lifecycle stop" }] } ],
        "SessionEnd":   [ { "hooks": [{ "type": "command",
            "command": "/home/lumen/foreman/harness/guard.sh --lifecycle end" }] } ]
      }
    }

Deny-rule caveat (why the hook exists): permission Bash rules are prefix matchers,
evadable via quoting and `bash -c` wrapping (RESEARCH §4). They are the polite first
layer; guard.sh is the wall. `Bash(docker *)` deny sits after the two docker allows;
allow rules win for exactly `docker ps*`/`docker logs*`, deny eats the rest.
`Bash(curl *)` allow is scoped tighter at build time (localhost ntfy + Bes ingest);
exact rule strings get a settings-lint unit test against the documented matcher
semantics of 2.1.220, not assumed.

## 5. guard.sh — PreToolUse guard, fail-closed

Contract: reads the hook JSON envelope on stdin. Exit 0 = allow, exit 2 = block with
reason on stderr (fed back to the model). ANY internal error, unparseable input, or
unknown shape = exit 2. jq required; jq missing = exit 2.

Checks, in order:
1. Host scope: `hostname` must be `lumen`. Foreman workers run on Lumen only.
2. Lifecycle mode (`--lifecycle start|stop|end`): append one JSONL line to
   /home/lumen/foreman/events/$FOREMAN_WORKER.jsonl
   `{ts, worker, event, session_id, effort: $CLAUDE_EFFORT, cwd}` and exit 0. This is
   the effort-verification channel (INTERNALS §A: $CLAUDE_EFFORT is the resolved
   per-turn value) and the restart-surviving event feed (RESEARCH §5 channel 2).
3. Bash commands (tool_input.command, normalized: collapse whitespace, strip quoting
   tricks by ALSO scanning the raw string) — block on ERE classes:
   - `(^|[;&| ])(sudo|reboot|shutdown|poweroff|halt|init [06])\b`
   - `systemctl +(stop|disable|mask)\b`
   - `\bdd\b +[^|;]*of=`
   - `--remove-orphans`
   - `\bip +link +set\b` ; `\bmacchanger\b`
   - `git +push +.*(--force\b|--force-with-lease\b|-f\b)`
   - `\brm\b` with both r and f flags AND an absolute-path target outside the worker's
     worktree and /tmp (worktree root passed via env by fworker)
   - `\bdocker\b` unless followed by `ps\b` or `logs\b`
   - `\bmkfs` , `\bwipefs\b`
   - `claude +setup-token` (rotates the account refresh token, GH #48786)
   - `(^|[;&| ])(ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL)=` (no in-command re-injection)
4. Read/Edit/Write (tool_input.file_path): block `.env`, `.credentials.json`,
   `claude_credentials.md`, anything under /home/lumen/agent/, ~/.claude/ (except the
   session's own transcript dir is not needed by workers at all → whole dir blocked).
5. Blocked calls append a JSONL line (event `blocked`, the offending command truncated)
   before exit 2. Allowed Bash calls are NOT logged per-call (volume).

Unit tests (zero quota): a corpus of must-block strings (incl. `bash -c "docker compose
--remove-orphans"`, quoted/whitespace-mangled variants, `sudo -i`, forced push forms)
and must-allow strings (`git push origin branch`, `docker ps -a`, `rm -rf
<worktree>/build`). Guard failures on the corpus fail the build probe.

**wq#729 addendum (2026-08-21):** allowed Bash calls that don't mutate shell state (no
`cd`/`export`/`source`/backgrounding `&`, …) are additionally rewritten via the
`PreToolUse` `updatedInput` field — `{ <cmd>\n} 2>&1 | head -c 2000` — so a single
discovery/dump command can't carry the whole session's cost on every later call
(research/CALL_AUDIT.md: tool results are 39.5-51.5% of a build session). Confirmed
supported on CLI 2.1.233 from the live hooks docs, not the CLI bundle (still out of a
build worker's reach). Stateful commands are skipped rather than capped: piping subshells
the left side, so a `cd`/`export` inside it wouldn't persist to the next Bash call.

**wq#730 addendum (2026-08-21):** same `PreToolUse` `updatedInput` lever, applied to
`Read` (research/CALL_AUDIT.md R3/R5/R6 — Read is the single most expensive tool in every
audited session, and 61 of 152 reads pulled a whole file). Three checks, all scoped to
the `Read` tool only:
- **R3** — a `Read` with no `limit` gets one injected (`FOREMAN_READ_LINE_CAP`, default
  300 lines), same shape as R4's Bash cap: files under the cap pass through untouched,
  only genuinely large whole-file reads get bounded.
- **R5** — an exact-repeat read (same path/offset/limit) of a file whose mtime hasn't
  changed since this session last read it is blocked, not re-fetched. Per-session state
  lives in a JSONL cache under `$FOREMAN_EVENTS_DIR/read_cache/<session_id>.jsonl`; an
  mtime change (an `Edit`/`Write`, or any out-of-band change) invalidates the entry.
- **R6** — a `Read` on a path that can't possibly succeed (missing, or a directory) is
  denied before it runs, so the guaranteed error never becomes a full-size tool_result
  carried for the rest of the session; only this unambiguous case is caught; anything
  permission- or content-shaped is left to the real tool.

## 6. Supervisor — stream-json parsing

fworker reads child stdout line by line (NDJSON), with `--include-hook-events`:
- `system/init`: capture session_id (resume key), model, permissionMode. Sanity-assert
  model == dispatched model; mismatch = flag in run state (entitlement drift, #79337).
- every event: bump last_event_ts. Inactivity watchdog: no event for N min
  (default 10) = kill child, classify HANG (CRASH class).
- `rate_limit_event`: track latest {status, rateLimitType, resetsAt, utilization}.
  status `rejected` = set saw_limit + forward the frame to the governor
  (`foreman_usaged.py report-event <json>`, T1's in-band override channel).
- `api_error`: saw_limit if error.rate_limits != null or category == "rate_limit".
- `model_refusal_fallback` / `model_refusal_no_fallback` / `model_fallback` /
  `api_retry`: log to run state; refusal_no_fallback marks the attempt REFUSED
  (counts as a strike, INTERNALS §C).
- `compact_boundary`: count; 2 compactions in one ticket = flag oversized → on exit,
  wq block with "decompose" note (RESEARCH §16) instead of a blind retry.
- final `result` frame: subtype + is_error + total_cost_usd + num_turns + modelUsage
  recorded in attempt-N.json.

## 7. Classification and the wq lifecycle

Classifier (INTERNALS §E, verbatim; fail-closed):

    SUCCESS  <= subtype success, is_error false, no refusal-ended turn
    REFUSED  <= subtype success + refusal signals (prose decline / no_fallback frame)
    TURNS    <= subtype error_max_turns
    BUDGET   <= subtype error_max_budget_usd
    LIMIT    <= any error/no-result AND saw_limit          (use resetsAt)
    CRASH    <= any other error, nonzero exit without result frame, HANG, reap
    ambiguous => LIMIT (pause, never burn the recovering window)

Ticket flow: fworker claims before spawn (§3). The WORKER runs `wq done` itself with the
PR url (existing lane contract). After child exit fworker reconciles:
- SUCCESS + ticket done in wq: append board line if the worker forgot, archive run state,
  remove worktree, exit 0.
- SUCCESS but ticket still claimed: worker lied or died at the finish line → verify PR
  state via lumen-pr check; if landed, `wq done` with notes; else treat as CRASH.
- TURNS/BUDGET/CRASH/REFUSED: attempt++ recorded with transcript path
  (~/.claude/projects/<dash-cwd>/<session>.jsonl). attempts < 2: restart (below).
  attempts >= 2: `wq block <ID>` with transcript path + last error, ntfy, exit.
- LIMIT: report-event already sent; if resets_at - now <= RESUME_WAIT_MAX (default 6h)
  fworker sleeps (heartbeating wq every 10 min) then RESUMES; else `wq release <ID>`,
  persist resume state, exit LIMIT so the orchestrator reschedules after reset.
- Reaped/killed fworker: lease expiry returns the ticket loudly (wq machinery); on next
  start fworker `run --resume` reconciles from _runs/ + `claude agents --json` + events
  JSONL before claiming anything.

Heartbeat: every 10 min while the child runs (promotes the provisional lease and keeps
it alive; 240m full lease vs 3600s default wall cap is comfortable).

Resume vs restart (RESEARCH §12/§13):
- resume-after-LIMIT: same worktree, `claude -p --resume <session_id> "continue: <one
  line of remaining acceptance>"` + same settings/env. Resume keeps the transcript's
  model; expect an immediate autocompact on a fat session.
- restart-after-CRASH: FRESH worktree (attempt N+1), prior-attempt-notes.md generated by
  fworker (last 20 assistant/tool events summarized mechanically + `git -C <old-wt> diff
  --stat`) and injected into the prompt. Never resume a crashed session.

## 8. Restart reconciliation

fworker is stateless across its own restarts except _runs/ + events JSONL:
attempt-N.json is written atomically at every state transition (spawned, init-captured,
classified, wq-actioned). `fworker run --resume --ticket <ID>` trusts, in order:
wq status for the ticket > _runs/ state > `claude agents --json` liveness > events JSONL
tail. No orphan claude child is ever adopted: if a live child exists for the session_id
(pid from attempt-N.json, cmdline sanity-checked), fworker re-attaches WATCH-ONLY
(waitpid impossible → poll /proc + transcript growth) and resumes the wq lifecycle.

## 9. Governor integration (T1 contract)

- Pre-spawn: STOP file check, then `foreman_usaged.py can-dispatch <model>`; non-zero
  exit = do not spawn cloud, exit DEFERRED (orchestrator's problem, not a strike).
- In-flight: rejected rate_limit_event frames forwarded via `report-event` (§6).
- STOP appearing mid-run does NOT kill a running worker (finish the ticket, the
  kill-switch halts DISPATCH); documented so nobody "fixes" it later.
- Governor down/stale: can-dispatch already fails closed to local-rung-only; fworker
  treats its absence (nonzero/no state.json) the same way.

## 10. OTEL → Bes

CONFIRMED live this session: Bes vmagent v1.147.0 at http://100.110.156.91:8429,
`POST /opentelemetry/v1/metrics` returns 200 (vmagent forwards to the fleet VM). Worker
env:

    CLAUDE_CODE_ENABLE_TELEMETRY=1
    OTEL_METRICS_EXPORTER=otlp
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
    OTEL_EXPORTER_OTLP_ENDPOINT=http://100.110.156.91:8429/opentelemetry
    OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative   # VM drops delta
    OTEL_METRIC_EXPORT_INTERVAL=60000
    OTEL_RESOURCE_ATTRIBUTES=service.name=foreman-worker,worker.id=<id>,wq.ticket=<ID>,gen.model=<model>,gen.effort=<effort>

Per-run token/cost truth for CAL-1 comes from these series grouped by resource attrs;
the usage wall stays the governor's job (§9).

## 11. gh-lane-worker retrofit

/home/lumen/bin/gh-lane-worker line 56 currently:
`claude -p --permission-mode bypassPermissions --model sonnet "$PROMPT"`.
Retrofit (mechanical, same ticket build phase, .bak first):

    env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL \
      timeout 3600 claude -p "$PROMPT" </dev/null \
        --model claude-sonnet-5 --effort high \
        --settings /home/lumen/foreman/harness/worker-settings.json \
        --permission-mode acceptEdits \
        --add-dir /home/lumen/agents/work-queue

Its own loop/sentinel logic stays. Its prompt gains one line telling it hook-blocked
commands mean "re-scope, do not retry". Full fworker adoption for the github lane is
follow-up work, not this ticket. Risk: the pack's deny/guard may block a verb the lane
legitimately used under bypass; mitigation = run one historical github ticket in the
verify pass and fix allow rules, never the guard classes.

## 12. Adversarial verify plan (build-phase probe gate)

Zero-quota unit level (probe_wq614.py):
1. Classifier: canned stream fixtures (rejected rate_limit_event, api_error with
   rate_limits, error_max_turns, error_max_budget_usd, bare error_during_execution,
   no-result kill) → LIMIT/TURNS/BUDGET/CRASH exactly per §7; synthetic rejected frame
   MUST classify LIMIT not CRASH.
2. guard.sh corpus (§5): every deny class blocked incl. `docker compose
   --remove-orphans` and `bash -c` / quoting evasions; allow corpus passes; malformed
   stdin exits 2.
3. Settings lint: worker-settings.json parses, mode acceptEdits, zero occurrences of
   bypassPermissions across harness/.
4. STOP + can-dispatch deny → fworker refuses to spawn (mock governor).
Quota level (one small run, sonnet-5@low or haiku, off-peak):
5. Scratch fixture ticket (mini CAL-1 shape: scratch repo + scratch wq DB via WQ_DB)
   through fworker end to end: claim → worktree → PR-shaped diff → done; events JSONL
   contains start/stop lines whose effort field equals the dispatched effort; worktree
   isolated (parent repo status untouched); transcript path recorded.
6. gh-lane-worker retrofit smoke on one trivial github-lane ticket.

## 13. Build-time checks and open questions for Frank

Checks folded into the build (no design impact):
- Do plain `-p` children show in `claude agents --json`? (README note either way.)
- Exact allow-rule matcher semantics for the two docker allows vs the docker deny on
  2.1.220 (settings-lint test proves it, §4).
- `--add-dir` interplay with acceptEdits Write scope outside cwd.

Frank decisions requested (block the BUILD, not this doc):
1. RESUME_WAIT_MAX default 6h (fworker sleeps through a limit inside one lumen-run unit
   vs exiting and letting the orchestrator reschedule). OK?
2. Worker Bash allow includes `curl` scoped to localhost ntfy + Bes ingest only — any
   other endpoints workers legitimately need?
3. gh-lane-worker retrofit lands with this build (brief github-lane pause + .bak). OK?
4. Housekeeping surfaced during this ticket: wq#615-618 carry descriptive labels
   ("human", "probe") in the wq gate field, which is a PATH precondition since wq#582 —
   as filed they refuse every claim. 614's was corrected to claim it (label moved into
   detail). Recommend the foreman re-file 616-618 gates as paths (e.g.
   `/home/lumen/foreman/harness/probe_ok:exists`) or empty + gate-in-detail.
