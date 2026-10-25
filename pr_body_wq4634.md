# fworker: re-issue ceiling-killed Bash calls via watchdog and resume the session

## What

When a `claude -p` child's Bash call is killed by the tool ceiling, fworker now
detects it in stream-json, re-runs the command under `/home/lumen/bin/watchdog
--no-send` (detached setsid), waits for its `result.json`, and resumes the same
session with the verdict via `claude -p --resume`. New module
`harness/tool_ceiling.py`, wiring in `harness/fworker.py`, design doc
`harness/TOOL_CEILING_REISSUE.md`.

## Why

A ceiling kill is a harness/transport artifact, not the command's failure (kill
mechanism family: `reports/wq3264_mcpw_commander_drops_rca.md` — supergateway
group reap / Worker 70s abort; locally, the CLI's own Bash timeout). The model
that sees the error either retries into the same ceiling or "fixes" healthy
code. Today that burns attempts and misdiagnoses tickets.

## How

1. **Detect** — `CeilingTracker` pairs `tool_use`→`tool_result` frames (the
   `LoopTracker` pattern) and fires on evidence-anchored kill classes. One ships
   built-in: `\ACommand timed out after ` (verified against
   code.claude.com/docs/en/hooks); new classes land via
   `FOREMAN_CEILING_KILL_PATTERNS` once a real frame is captured — no guessed
   signatures. `run_child` stops the session with `killed_reason="tool_ceiling"`.
2. **Re-issue** — `tool_ceiling.reissue()` runs watchdog with the child's env and
   worktree cwd, label `fw<ticket>-a<attempt>r<n>`, a one-shot localhost
   `--callback` sink for instant wake plus `result.json` polling as the
   correctness path, bounded by `timeout+slack`, heartbeating the wq lease
   throughout.
3. **Resume** — the verdict (or an honest advisory on every failure mode) rides
   `claude -p --resume <session_id>` as the prompt — the wq#2138 steer channel.
   Same attempt number; never charged to the ticket.

Rails: per-attempt budget (`FOREMAN_CEILING_REISSUE_MAX`, default 2); a
never-reissue list for publishing commands (`git push`, `lumen-pr
commit/open/land`, `wq done/block/release`) which get an advisory instead of a
blind double-apply; forensics record per re-issue in the run dir plus
`ceiling_reissues`/`ceiling_hit` on the attempt record.

## Testing

`python3 -m pytest harness/tests/test_tool_ceiling.py -q` — **20 passed**:
detection positives, five negative controls (anchor, non-error, non-Bash,
budget, ordinary failure), pattern-file extension + malformed file, stub-watchdog
failure injection (refusal, missing binary, verdict never arrives → bounded
still-running advisory, denylist proves no invocation), `run_child` integration
over real subprocess stream-json plumbing, and three tests against the real
`/home/lumen/bin/watchdog` (OK, FAIL exit 7, re-run driven into watchdog's own
TIMEOUT kill). Neighboring suites (`test_loop_break_integration.py`,
`test_heredoc_steer.py`) pass; full harness suite run alongside.

## Risk / rollback

Additive: detection only activates on a `ceiling_tracker` hit; with no hit every
run path is byte-identical. Misdetection risk is bounded by the anchored regex +
negative-control tests; a bad re-run is bounded by the budget and the
never-reissue list. Rollback: revert the commit, or set
`FOREMAN_CEILING_REISSUE_MAX=0` to disable detection without a deploy.

Refs: wq#4634

🤖 Generated with [Claude Code](https://claude.com/claude-code)
