# wq#4591 classifier RCA — KILLED "no mechanical signature" on allow-listed ls/wq (wq#4683)

Back-reference: design/orchestrator_verify_lane.md §8 item 2 (filed as wq#4683, S1.2).
Run under RCA: `_runs/wq4591/` (attempt-2 session `539d080c-d10d-4b8d-ada6-fac1df405216`).

## Verdict (one paragraph)

Two independent defects stacked. (1) The two denied calls in `failure.json` were
refused because of **invocation shape**, not allowlist gaps: a stderr-redirect
(`2>/dev/null`) inside a compound segment defeats `Bash(ls *)` matching (known
upstream Claude Code bug), and an env-assignment prefix (`WQ_DB=… WQ_AGENT=…`)
in front of `/home/lumen/bin/wq` defeats the prefix rule `Bash(/home/lumen/bin/wq *)`.
(2) Neither of those killed the run. The kill was a loop-break on 3× repeated
**PreToolUse hook denials** whose text matches none of `action_gate.PERMISSION_DENIAL_RE`'s
three markers, so the LoopTracker tripped with `kind=None` → fworker classified
KILLED/INFRA_CRASH instead of PERMISSION_LOOP, and `failure_record.triage()` had no
gate that admits plain `requires approval` denials — it fell through to
`TRIAGE_UNKNOWN "no mechanical signature matched"` (failure_record.py:438).
The design doc's hypothesis ("cd-chained / quoted invocations",
design/orchestrator_verify_lane.md §8) is **not** what happened.

## Exact transcript lines

Extracted from `~/.claude/projects/-home-lumen-worktrees-wq4591-a2/539d080c-…jsonl`
through the mask-at-read path (wq#2275/wq#2546: `harness/masking.mask_text` applied
to every printed string; raw transcript never emitted). Re-runnable probes:
`tools/wq4591_rca/*.py` in this repo.

**Denial 1 — `ls` (JSONL lines 32→33):**

```
CMD: ls -la /home/lumen/foreform 2>/dev/null; ls /home/lumen/foreman/reports/ 2>/dev/null | grep -i 4591; echo "---"; ls /home/lumen/foreman/reports/ 2>/dev/null | grep -i 4577
ERR: This Bash command contains multiple operations. The following part requires approval: ls -la /home/lumen/foreform ; ls /home/lumen/foreman/reports/
```

`grep` and `echo` segments passed; only the two `ls … 2>/dev/null` segments were
refused. `Bash(ls *)` was in the pack in effect — confirmed in the run's own
snapshots: `_runs/wq4591/settings-foreman-a7d28251e12d.json:26` and
`_runs/wq4591/snapshots/20261023T103812Z-…worker-settings.json:29`.

**Denial 2 — `wq add` (JSONL lines 156→157):**

```
CMD: WQ_DB=/tmp/fworker-wq4591/scratch/queue.db WQ_AGENT=wq4591-verify-agent /home/lumen/bin/wq add --lane auto --title "wq4591 scratch probe" 2>&1
ERR: This command requires approval
```

`Bash(/home/lumen/bin/wq *)` was in the same pack (settings snapshot line 11 /
worker-settings line 16).

**The only other refusal frames persisted in the whole 78-turn session:**

```
line 57:  Claude requested permissions to use mcp__gitea__pull_request_read, but you haven't granted it yet.
line 153: PreToolUse:Bash hook error: [/home/lumen/foreman/harness/guard.sh]: guard.sh: BLOCKED — rm -rf outside worktree/tmp denied
```

**The frames that actually tripped the kill are not in the persisted JSONL.**
`tools/wq4591_rca/scan_sessions.py` finds zero occurrences of the loop-reason text
("bypass the intent behind this denial…") in any JSONL under the wq4591-a2 project
dir; `tools/wq4591_rca/tail_frames.py` shows the file ends at line 236–237 (a failed
`ScheduleWakeup`), while the loop-handoff (`_runs/wq4591/loop-handoff-attempt-2.md`)
records ~10 later tool calls (failed Read of `~/.claude/projects/...tool-results/…`,
failed `mkdir && python3 - <<EOF` heredoc). fworker killed the CLI with SIGTERM
(exit 143) on the trip, and the transcript tail was never flushed. The classifier
was *not* starved by this: fworker passes its **live in-memory stream frames** to
`build_failure_record` (fworker.py:2970/3109), which is why
`failure.json.last_error_sig` carries the boilerplate the persisted file lacks.

## RCA part A — why allow-listed verbs were refused

**A1. `ls` + stderr redirect.** Claude Code's permission walker fails to match
allow rules against command parts containing redirections — upstream
anthropics/claude-code issues #13607 ("Bash commands with stderr redirection
(2>/dev/null) require permission despite whitelisted base commands") and #47701
("Permission walker unhandled node type `file_redirect` blocks allow-list for
commands with redirects/pipes"). Found via fleet-search (doctrine prior-art step).
Note the refusal message itself prints the parts with the redirects stripped,
which is why the shape is invisible in the error text alone.

**A2. `wq` + env-assignment prefix.** The CLI strips a fixed wrapper set
(e.g. `timeout`) before matching but not `VAR=value` assignment prefixes, so the
command string starts with `WQ_DB=…`, which no rule prefix-matches. Empirical:
this exact frame (156→157) with the rule provably in the pack.

**A3. The extractor then hides both shapes.** `failure_record._argv()` strips
env-assignment prefixes and wrappers (failure_record.py:170-178) before computing
`argv0`, and `build_failure_record` drops the `argv` list when writing rows
(failure_record.py:477 keeps only tool/argv0/target/refusal/count). So
`failure.json` reports `argv0: wq` — the normalized verb that *would have been*
allowed — and discards the prefix/redirect that caused the refusal. The RCA signal
is destroyed at record-write time.

## RCA part B — why there is no mechanical signature

Chain, with citations:

1. The three tripping errors were hook/permission denials whose text ends in the
   CLI boilerplate "…bypass the intent behind this denial. if you believe this
   capability is essential … let the user decide how to proceed."
   `action_gate.PERMISSION_DENIAL_RE` (action_gate.py:867-868) knows only
   `requires? approval | requires? permission | blocked\. for security` — it
   matches none of: the hook-deny boilerplate, guard.sh's `BLOCKED — denied path`,
   or the MCP grant message `Claude requested permissions to use …, but you
   haven't granted it yet` (which is also what killed attempt 1 in 5 turns).
2. So `harness_refusal=False` in `LoopTracker.observe` → the 3×-same-error trip
   fired with `kind=None` (action_gate.py:998-1001, `error_counts[esig] >= self.n`).
   `_error_sig` keeps only the last 240 chars, so three *different* hook denials
   collapse into one identical boilerplate signature — the loop breaker is right
   to trip, but blind to what kind of loop it is.
3. `fworker.loop_break_classification` (fworker.py:504-514) promotes a loop_break
   to PERMISSION_LOOP only when `trip_kind == "PERMISSION"`; otherwise the raw
   KILLED stands and lands in `_INFRA_CRASH_CLASSIFICATIONS` (fworker.py:499,
   521-522) → `block_class: INFRA_CRASH`, charged against infra.
4. `failure_record.triage` (failure_record.py:402) admits a run into the
   capability-denied branch only on `trip_kind == "PERMISSION"`, capability argv0s
   (`ssh`/`sudo`/`systemctl`/bench), structural refusals, or unreachable paths.
   The two real denials are plain `requires approval` on `ls`/`wq` — none of the
   four. The `elif denied_calls:` head at :414 ("permission-refused Nx") exists
   but is unreachable for this shape because the :402 gate never opens. Falls
   through to :438 → `UNKNOWN: no mechanical signature matched`.

## Proposed signature

**S1 (primary — action_gate.py).** Extend the denial recognizer with the hook/grant
markers, as a second regex used by both `LoopTracker.observe` and
`failure_record.extract_denied_calls`:

```python
HOOK_DENIAL_RE = re.compile(
    r"bypass the intent behind this denial"      # CLI PreToolUse-deny suffix
    r"|PreToolUse:\w+ hook error"                # hook deny head
    r"|hook error: .{0,80}BLOCKED"               # guard.sh deny
    r"|requested permissions to use \S+, but you haven't granted", re.I)
```

`is_permission_denial(text)` returns True for either regex. Effect on a wq4591
replay: LoopTracker trips with `kind="PERMISSION"` → fworker classifies
PERMISSION_LOOP (harness defect, no infra circuit-breaker charge) → triage enters
the :402 branch and emits `permission-refused Nx (…)` instead of
`KILLED: no mechanical signature matched`. This also fixes attempt 1's shape
(MCP grant loop) for free. In `extract_denied_calls`, tag these rows
`refusal: "hook_denied"` so deliberate guard policy stays distinguishable from
allowlist gaps in `inferred_needs`.

**S2 (backstop — failure_record.py:402).** Admit plain denials when the run died:
`if trip_kind == "PERMISSION" or capability_hits or unreachable_paths or
structural_hits or (classification == "KILLED" and denied_calls):` — the existing
:414 head already renders it. Scoped to KILLED so a stray denial in a successful
run still doesn't dominate triage.

**S3 (shape visibility — failure_record.py:477).** Keep `argv` (already capped at
6 tokens, masked at read) in the written row, and add a `shape` tag computed
before `_argv()` normalization: `env-prefix` when the raw segment starts with
`\w+=`, `redirect` when it contains `2>`/`>`/`<`. A row like
`{argv0: wq, refusal: requires approval, shape: env-prefix}` makes the next RCA a
one-line read and gives triage a head of the form
"allow-listed verb refused by invocation shape (env-prefix)".

**S4 (prevention — worker prompt RULES).** Two shapes to add next to the existing
compound-segment warning: never prefix an allow-listed command with `VAR=value`
assignments (put values in a file or pass as flags), and avoid `2>/dev/null`
inside allow-listed segments until upstream #13607/#47701 land (stderr shares the
2000-char cap anyway).

## Correction to the design doc

design/orchestrator_verify_lane.md §8 item 2 says "the pattern match misses
cd-chained/quoted invocations of allowed tools". Neither denied call was
cd-chained or quote-mangled; the shapes are stderr-redirect (upstream matcher bug)
and env-assignment prefix. S1.2 should be re-pointed at this report.
