# Tool-ceiling re-issue: recover a Bash call killed by the harness, not the command (wq#4634)

Design-first per fleet doctrine. Implementation: `harness/tool_ceiling.py`, wiring in
`harness/fworker.py` (run_child frame loop + do_attempt resume branch). Tests:
`harness/tests/test_tool_ceiling.py`.

## 1. Problem

A `claude -p` child's Bash call can be killed by a ceiling that has nothing to do with
the command: the kill-mechanism family is documented in
`reports/wq3264_mcpw_commander_drops_rca.md` (supergateway's per-request process-group
reap, triggered by the Worker's 70s `tools/call` abort), and for a local fworker child
the everyday observable is the CLI's own Bash tool timeout. Either way the model sees an
error `tool_result` and then does the wrong thing with high confidence:

- retries the same command into the same ceiling (a loop_break kill later, a burned
  attempt), or
- misdiagnoses a healthy long command as broken and "fixes" working code.

A 10-minute test run or build is a legitimate tool call; the ceiling is a harness
artifact. The ticket's demand: detect the kill class in stream-json, re-issue the
command under `/home/lumen/bin/watchdog`, wait for its `result.json`, and resume the
same session with the verdict. (The ticket also refs `scratch/research/
watchdog-long-command.md` §3D/§9 — that file does not exist in the repo, its git
history, or any granted directory; this design derives from the wq#3264 RCA and
`research/wq4629_commander_watchdog_survival.md` instead, and the gap is logged in
`kb/ISSUE-LOG.md`.)

## 2. Prior art this design stands on

- **wq#2138 steer** (`fworker.py` `killed_reason == "steer"` branch): the proven channel
  for talking to a `-p` child — it has no stdin, so you stop the session and
  `claude -p --resume <session_id>` with your message as the prompt. fworker already
  passes `--resume` (`build_claude_cmd`). The re-issue verdict rides exactly this
  channel, and like steer it keeps the same attempt number: a harness-caused kill is
  never charged to the ticket.
- **`bin/watchdog` (wq#4629)**: daemonises itself `setsid`+`nohup` — a new session, which
  is precisely what survives the RCA's group reap (`reports/wq3264...:22-25`: processes
  in their own session are spared) — supervises the command in its own process group,
  and reports once via `result.json`, `--callback` POST, and friends. `--no-send` makes
  it file/callback-only, which is all a headless harness needs.
- **`action_gate.LoopTracker`**: the frame-pairing pattern (pending `tool_use` id →
  `tool_result`) that `CeilingTracker` copies.
- **wq#4629 research §6**: nothing can push into a live model turn — so "wait for the
  result without killing the session" is not an option; stop-and-resume is the only
  real channel.

## 3. Design

### 3.1 Detection — `CeilingTracker`

Fed every frame `run_child()` parses, next to `loop_tracker`. Pairs each `tool_use`
carrying an `input.command` with its `tool_result`; on `is_error`, matches the result
text against the kill-class table. On a match, `run_child` stops the session with
`killed_reason="tool_ceiling"` (same `_kill_proc_tree` path as every other kill) and
`tracker.hit` carries `{class, tool, command, error}`.

**Kill classes are evidence-driven.** Only one ships built-in:

| class | tool | error_re | evidence |
|---|---|---|---|
| `bash_timeout` | `Bash` | `\ACommand timed out after ` | code.claude.com/docs/en/hooks (PostToolUseFailure example: "Command timed out after 2m 0s") |

The regex is anchored at the start of the result text: the CLI puts its timeout banner
first, and anchoring is the negative control that keeps a failing test suite that merely
*prints* the phrase from being mistaken for a ceiling kill (tested).

Deliberately NOT shipped: an MCP-abort class (`MCP error -32001` etc.) for the
supergateway/Worker kill itself. No captured frame exists anywhere in the fleet to pin
its text to (grepped `_runs/`, reports, kb), and a guessed signature is exactly the
fabricated-evidence failure mode instinct 8 / wq#2795 warn about. When one is observed,
it lands without a code change via `FOREMAN_CEILING_KILL_PATTERNS=<json file>`:
`[{"class": ..., "tool_re": ..., "error_re": ...}]` (tested, including the
malformed-file-keeps-builtins path).

### 3.2 Re-issue — `tool_ceiling.reissue()`

Called from `do_attempt()` right after the steer branch, before classification —
so the killed segment, like a steered one, writes no attempt record and costs no attempt:

```
watchdog --no-send --label fw<ticket>-a<attempt>r<n> --timeout <REISSUE_TIMEOUT_MIN> \
         --poll <s> --cwd <worktree> --callback http://127.0.0.1:<port>/ -- <command>
```

- Runs with the child's own `env` and the worktree as cwd — the same context the
  original Bash call had.
- `--timeout` is `FOREMAN_CEILING_REISSUE_TIMEOUT_MIN` (default 30 min), not the
  original call's timeout: the original timeout *was* the ceiling that killed it.
- **Wait**: a one-shot localhost HTTP sink (`--callback`) wakes the waiter the moment
  watchdog POSTs the verdict; polling `result.json` every `poll_s` is the correctness
  path (the sink is latency only, and its failure to bind degrades to pure polling).
  The wait is bounded by `timeout_min*60 + FOREMAN_CEILING_WAIT_SLACK_S` (default 120s)
  — watchdog itself writes a TIMEOUT `result.json` at its own ceiling, so slack only
  covers watchdog's death. While waiting, fworker heartbeats the wq lease every
  `HEARTBEAT_INTERVAL_S` so a long re-run cannot lose the claim.
- `result.json` parse retries briefly (jq writes via redirect; a reader can catch a
  partial file).
- A forensics record `ceiling-reissue-<label>.json` lands in the ticket's run dir for
  every outcome, and the attempt record carries `ceiling_reissues`/`ceiling_hit`.

### 3.3 Resume

`do_attempt(..., resume_session=state.session_id, resume_text=<verdict>)` — the verdict
line becomes the resumed session's prompt (masked through `masking.mask_text` first;
the frames it derives from were already masked in `run_child`, this is defense in
depth). Two flavors:

- **verdict** (re-run completed): status/exit/note + the `.out`/`.tail` paths, with an
  explicit "Do NOT re-run the command".
- **advisory** (not re-run / couldn't start / verdict never arrived): tells the session
  the call was ceiling-killed, what fworker did or couldn't do, and to verify state
  itself — strictly better than letting a dead session's work evaporate.

### 3.4 Safety rails

- **Budget**: `FOREMAN_CEILING_REISSUE_MAX` (default 2) re-issues per attempt, threaded
  through the recursion; at 0 the tracker goes silent and further kills classify
  normally. No unbounded kill→re-issue→resume loop is possible.
- **Never-reissue list** (`NEVER_REISSUE_RE`): `git push`, `lumen-pr commit/open/land`,
  `wq done/block/release` — anything where a half-applied first try plus an automatic
  second try can double-apply. Those get the advisory, not a re-run. (The command
  already passed the PreToolUse gate once — re-running an *approved* command is in
  policy; re-running a *publishing* one blindly is not.)
- **Idempotency caveat**: for everything else, a mid-kill leaves partial effects and the
  re-run repeats them. Accepted: the command population here is overwhelmingly
  builds/tests/waits (the RCA's own category table: bash/sleep/wait), the verdict names
  the command so the resumed session can sanity-check, and the budget caps the damage.
- **Label collision**: labels are deterministic (`fw<ticket>-a<attempt>r<n>`) for
  forensics; a stale live watchdog with the same label makes the spawn fail loudly
  (watchdog's own PID-file check) → advisory, never a silent mix-up.

## 4. Alternatives rejected

- **Keep the session alive and wait out the long command**: impossible — the kill
  already happened inside the CLI/transport, and nothing can push into a live turn
  (wq#4629 research §6).
- **`lumen-run` (systemd-run) instead of watchdog**: survives the reaper equally well,
  but is pull-only (no callback/result.json contract), needs sudo systemd-run, and the
  ticket specifies the watchdog contract. watchdog's setsid daemonisation is sufficient
  for every kill class in the RCA.
- **Teach the worker prompt to always use watchdog/lumen-run for long commands**:
  already doctrine, still leaks — this is the harness-side backstop for when the model
  gets it wrong, mirroring the loop-breaker's role ("what Frank had to do manually").
- **Detect by exit code 143/137 in the result text**: unverified signature and false
  positives (OOM, tests that exit 137 themselves) — left to the pattern file once a
  real frame is captured.

## 5. Scope and known limitations

- Direct-spawn `run_child()` only. The gateway-native path maps frames but acts on no
  trackers (steer has the same limitation); its *fallback* to run_child carries the
  tracker through. The aider runtime emits no stream-json — not applicable.
- A ceiling kill with no `session_id` yet (init frame never arrived) falls through to
  normal classification — nothing to resume.
- `run_fire_child` is out of scope (its attempt numbering is separate; same reasoning
  as the SIGTERM-context exclusion documented there).

## 6. Test-to-failure map (`harness/tests/test_tool_ceiling.py`, 20 tests)

| branch | test | forced failure |
|---|---|---|
| detection fires | `test_ceiling_kill_detected` | — |
| detection must NOT fire | `..._ordinary_failure` / `..._phrase_mid_output` / `..._successful_result` / `..._non_bash_tool` / `..._budget_exhausted` | negative controls incl. the anchor |
| pattern extension | `..._extension_patterns_via_env` / `..._malformed_extension_file` | malformed JSON file |
| re-issue happy path | `test_reissue_happy_path_verdict` | — |
| watchdog refuses / missing | `..._watchdog_refuses` / `..._watchdog_missing` | exit 3; ENOENT |
| watchdog dies silently | `test_reissue_verdict_never_arrives` | daemonizer OK, no result.json ever → bounded wait, still-running advisory |
| publishing command | `..._denylisted_command_not_rerun` | stub proves it was never invoked |
| run_child integration | `test_run_child_stops_on_ceiling_kill` / `..._ordinary_failure_untouched` | real subprocess + stream-json plumbing; session_id survives |
| real binary | `test_real_watchdog_ok` / `..._command_fails` / `..._rerun_times_out` | exit 7; the re-run itself driven into watchdog's TIMEOUT kill |
