# wq#4634 — re-issue ceiling-killed Bash calls via watchdog, then resume the session

## Problem

A `claude -p` child's long shell command can be killed by its transport, not by the
command failing:

- **Bash tool ceiling.** Claude Code kills a Bash call at its timeout and hands the
  model `Error: Command timed out after 2m 0.0s` (anthropics/claude-code#1717); SIGURG
  kills even `run_in_background` jobs (#34138, cited in `/home/lumen/bin/watchdog`
  header).
- **Commander path.** supergateway (stateless streamable-HTTP) reaps the request's
  whole process group the moment the HTTP response ends — Mode A: the call returned;
  Mode B: the Worker aborts every `tools/call` at 70 s
  (`reports/wq3264_mcpw_commander_drops_rca.md`). A 30-minute build mid-flight dies and
  the model reads a transport error.

The model cannot fix any of these by retrying: the same ceiling kills the retry. Today
that burns turns (and can trip the LoopTracker) on a command that was *working*.

## Prior art inside the harness

- **wq#2138 steer**: the first CLI brace-with-quote refusal stops the session
  (`killed_reason="steer"`, `fworker.py` run_child), and `do_attempt` resumes the same
  session (`--resume <session_id>`, same attempt number) with a corrective prompt
  (`action_gate.SHELL_REFUSAL_STEER`). This feature is that pattern with a verdict
  instead of a scolding.
- **wq#4629 watchdog**: `/home/lumen/bin/watchdog` daemonises (setsid+nohup → survives
  both the Bash-tool SIGKILL and the supergateway group reap, proven by the wq#3264
  setsid control row), supervises the command in its own process group, and writes
  `/tmp/watchdog-<label>.result.json` `{status, rc, note, duration_s, out, tail, ...}`
  at verdict time.

## Design

One new kill class in the stream-json loop, one reissue helper, one resume hop.

### 1. Detection (`action_gate.LoopTracker`)

The LoopTracker already pairs every `tool_use` with its `tool_result` and holds the
steer machinery, so ceiling detection lives there rather than in a parallel tracker:

- Track pending tool calls that carry a shell command: `Bash`, plus any MCP tool whose
  input has a string `command` (covers `mcp__desktop-commander__start_process`).
- On an `is_error` result for such a call whose text matches `CEILING_KILL_RE`, set
  `self.ceiling = {"command": ..., "error": ...}` once per session
  (`ceiling_spent` flag, mirroring `steered`).
- `CEILING_KILL_RE` signatures, each with provenance:
  - `command timed out after` — the Bash tool ceiling (#1717).
  - `(sigkill|sigterm) received` / `killed by signal` — signal-kill of a foreground or
    background Bash job (#34138 class).
  - `context canceled` — cloudflared/Worker abort as logged in the wq#3264 RCA.
  - `mcp error -32001` / `request timed out` — the MCP client's own timeout error for
    an aborted `tools/call`.
  The MCP patterns (`context canceled`, `mcp error -32001`, `request timed out`) only
  count when the tool name starts with `mcp__`: the same strings inside ordinary Bash
  OUTPUT (a failing Go test prints "context canceled") are the command's own business.
  The set is deliberately narrow: an ordinary nonzero exit, a compile error, or a
  permission refusal must NOT match (test-to-failure below). A miss costs nothing new
  (today's behaviour); a false positive would re-run a failed command outside the
  session, so precision wins over recall.

### 2. Kill + reissue (`fworker.py`)

- `run_child`: after `loop_tracker.observe(frame)`, if `loop_tracker.ceiling` is set →
  `killed_reason = "tool_ceiling"`, break (exactly the steer shape). The child is
  SIGTERM'd; the killed command is NOT running anyway — that is the premise.
- `do_attempt`: new branch next to the steer branch:

  ```
  if killed_reason == "tool_ceiling" and state.session_id:
      verdict = reissue_ceiling_killed(ticket_id, attempt, loop_tracker.ceiling,
                                       worktree)
      return do_attempt(args, attempt, resume_session=state.session_id,
                        prior_worktree=worktree, steer=verdict, ceiling_spent=True)
  ```

  Same attempt number — a transport kill must not cost the ticket an attempt
  (wq#2138 precedent). `ceiling_spent=True` threads into the resumed LoopTracker so a
  second ceiling kill in the same attempt chain is an ordinary error (no infinite
  reissue recursion; the resumed session is told to use watchdog itself next time).

- `reissue_ceiling_killed`:
  1. **Gate re-check**: the command ran once under the PreToolUse gate, but fworker
     re-running it directly bypasses hooks, so re-classify with
     `action_gate.classify_bash` against the compiled rules; a RED verdict refuses the
     reissue and the resume prompt says so.
  2. Spawn `/home/lumen/bin/watchdog --no-send --label fw-<ticket>-a<attempt>
     --cwd <worktree> -t <FOREMAN_REISSUE_TIMEOUT_MIN, default 30> -- <cmd>`.
     `--no-send` because there is no shared-terminal session to inject into; the
     verdict is consumed from `result.json`.
  3. **Wait for `/tmp/watchdog-fw-<ticket>-a<attempt>.result.json`** by polling
     (timeout × 60 + 120 s hard cap). The ticket sketch names a `--callback <local
     hook>`; fworker has no HTTP listener and `result.json` is written unconditionally
     before the callback fires (`watchdog:204-211` vs `:234-236`), so the file IS the
     synchronization artifact — the callback adds a dependency (an HTTP endpoint)
     without adding information. `--callback` stays available for a future
     Trigger.dev-style integration.
  4. Build the verdict line from `result.json` (masked via `masking.mask_text`):
     `[watchdog:<label>] OK|FAIL|TIMEOUT — <note> — output tail: <tail path>` plus one
     fixed instruction: the killed call was re-run to completion by the harness; read
     the tail, treat it as the command's true result, do not re-run it; for any future
     long command use `/home/lumen/bin/watchdog` yourself.
  5. Any failure (spawn error, wait timeout, unparsable result) degrades to a resume
     line that states the kill and the failure honestly — the session continues either
     way; fworker never silently swallows the event.

### 3. What stays out of scope

- No change to `run_child_gateway` / `run_child_aider` (no new signature anywhere —
  detection rides the loop_tracker they already take or don't take).
- No PTY/stdin injection into the live child: a `-p` child has no stdin (wq#2138), the
  stop→resume hop is the only delivery channel.
- Heartbeats keep flowing during the watchdog wait (the wait loop calls
  `wq_heartbeat` on the same cadence as run_child) so the claim does not look stale.

## Failure modes considered

| mode | handling |
|---|---|
| ceiling text in a *successful* result | only `is_error` results are examined |
| ordinary command failure matches | signatures are transport-specific; test-to-failure pins this |
| reissued command loops forever | watchdog `-t` ceiling kills its group; wait has a hard cap |
| reissue of a RED command | classify_bash re-check refuses; resume says why |
| second ceiling kill after resume | `ceiling_spent` — ordinary error path, no recursion |
| stale result.json from a prior run | label is per (ticket, attempt); watchdog refuses a live duplicate label and clears old files on daemonise (`watchdog:107`) |
| session_id never seen | branch requires `state.session_id`; otherwise normal kill classification |
