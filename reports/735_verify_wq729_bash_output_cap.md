# wq#735 — verify-pass: wq#729 [foreman-tok] T7 tool-output discipline lever — spike + imp

**Verdict: CONTRADICT** — the mechanism works as a truncation lever (CLI does honour
`updatedInput`, `head -c 2000` does cap bytes), but the wrapper it installs,
`{ <cmd>\n} 2>&1 | head -c 2000`, silently destroys the wrapped command's exit status on
**every** capped Bash call (not just large-output ones), and can additionally terminate
the wrapped command mid-execution via SIGPIPE before it finishes its work when total
output exceeds the pipe buffer — with no signal of either failure mode reaching the
agent. Both defects reproduce against the actual shipped `harness/guard.sh`, not a
re-implementation.

PR: `lumen/claude-foreman` #29, branch head `6980774` (`feat(harness): cap Bash tool
output via PreToolUse updatedInput`), merge-base `6ca5500` — `origin/main` has moved 9+
commits past that base, PR is still unmerged/stale at verify time.

## What was verified and holds up

- `python3 -m pytest harness/tests/test_guard.py -q` on the PR's own `guard.sh` +
  `test_guard.py` (checked out via `git show wq735-pr29:...`) → **13/13 pass**, matching
  the PR's own claim.
- `updatedInput` from `PreToolUse` is real and does rewrite the executed command — traced
  through the actual wrapped string the hook emits, not asserted from docs alone.
- The stateful-command exclusion list (`cd`/`export`/`source`/`&`/…) does what it says:
  regex correctly distinguishes `&` (background) from `&&` (logical AND), and correctly
  leaves `(cd ...)`/`$(cd ...)` uncapped-detection moot since those already subshell
  regardless of the wrapper.
- `head -c 2000` does truncate to exactly 2000 bytes and preserves normal (small, exit-0)
  command output verbatim, per the PR's own `test_wrapped_command_actually_truncates` /
  `test_wrapped_command_preserves_normal_output`.

## Contradiction — exit status is discarded on every capped call, not just large ones

`harness/guard.sh:178-181` (PR version) builds:

```
WRAPPED=$(printf '{ %s\n} 2>&1 | head -c %s' "$RAW_CMD" "$CAP_BYTES")
```

and hands that back as the command the harness actually executes. `$RAW_CMD | head -c N`
is a pipeline; without `pipefail` (nothing in this repo or the PR's own tests sets it —
grepped `harness/` for `pipefail`, only hit is unrelated `wq_worker_slot.sh`), the shell's
`$?` after the pipeline is **`head`'s** exit status, which is 0 whenever it reads
successfully — regardless of whether `$RAW_CMD` failed. This is not a large-output edge
case: it happens on every single capped invocation, including ones producing zero bytes.

Reproduced directly against the shipped `harness/guard.sh` (checked out from the PR
branch into this worktree, guard invoked for real, its `updatedInput` then actually
executed via `bash -c`, exactly what the harness does):

```
=== guard probe: false
  wrapped: { false
} 2>&1 | head -c 2000
  wrapped exit=0  direct(unwrapped) exit=1  MATCH=False
=== guard probe: exit 7
  wrapped exit=0  direct(unwrapped) exit=7  MATCH=False
=== guard probe: ls /no/such/path/xyz
  wrapped exit=0  direct(unwrapped) exit=2  MATCH=False
=== guard probe: grep zzzNOPE /etc/hostname
  wrapped exit=0  direct(unwrapped) exit=1  MATCH=False
```

Also added (and ran, then removed — scratch-only, not committed)
`harness/tests/test_wq735_exitcode_probe.py`, which feeds
`pytest_selftest_should_fail_marker; false` through the real `harness/guard.sh` on disk
and asserts the wrapped exit status matches the direct one. It fails:
`exit status masked: wrapped=0 direct=1`.

Since `cd`/`export`/`source`/background are the *only* exclusions, this covers the
overwhelming majority of real Bash calls a build worker makes — `pytest`, `git`, `grep`,
build/lint/deploy scripts, anything read-only or side-effecting-but-not-state-mutating.
Any of those that fails but is eligible for capping (almost all of them — the exclusion
list has nothing to do with success/failure) now reports **success** to the agent. This
directly undermines the exact doctrine both `harness/prompts/worker.md` and this fleet's
"Done means demonstrated: tests run and pass" rule depend on: a worker running `pytest`
under this hook cannot trust a 0 exit code to mean tests passed.

The PR's own test suite does not catch this: `test_must_allow_commands_still_exit_zero_
when_capped` (`harness/tests/test_guard.py`, added by this PR) asserts `guard.sh`'s own
process exit code is 0 for allowed commands — that is unrelated to, and does not
exercise, the wrapped shell command's exit status once the harness actually runs it. The
name reads like exit-status coverage; it isn't.

## Second, compounding defect — SIGPIPE can kill the wrapped command before it finishes

`head -c N` closes its read end once it has consumed `N` bytes. If `$RAW_CMD` keeps
writing past that point (common once its combined stdout+stderr exceeds the ~64KB pipe
buffer), the next write gets `SIGPIPE`/`EPIPE` and the process dies then and there —
**anything the command does after the point where output crosses the cap threshold
never runs**, silently, and (per the first defect) still reports exit 0.

Reproduced: a Python script writes ~12MB of stdout in a loop, then does one final
`open(marker, 'w').write(...)` as its last statement. Run unwrapped, the marker file is
written. Run through the exact wrapper `guard.sh` emits:

```
wrapped exit: 0   stdout_len: 2000
marker file exists (side effect completed)? False
```

The process was killed mid-run; the final write never happened; the harness sees a clean
exit. Before this PR, the harness's own display-level 2000-char cap (wq#954) truncated
what was *shown* after the command ran to completion — this PR moves the cap into the
live pipe, which changes semantics from "run to completion, hide the tail" to "may be
killed once chatty," for any command whose total output exceeds the cap.

## Disposition

Mechanism: `updatedInput` capping is real and does what the spike set out to confirm —
**confirmed**. Safety net for stateful commands (`cd`/`export`/`source`/`&`): correct —
**confirmed**. Wrapper correctness for the actual execution semantics the fleet depends
on (exit status, run-to-completion): **contradicted**, reproducibly, against the real
shipped file. Not fixing it myself — "do not re-implement" and the fix shape needs a
decision (add `pipefail` + surface a distinct sentinel for "was capped," and/or redirect
to a temp file and `head -c` the file instead of piping the live process) that's a design
call, not a mechanical patch. Escalating to opus-5@high per ticket instructions.

## Recommendation for the follow-up

1. Redirect instead of pipe: `{ <cmd>; } > /tmp/out 2>&1; rc=$?; head -c N /tmp/out; exit
   $rc` (or equivalent) so the wrapped command always runs to completion and its real
   exit status survives — the cap only affects what's *read back*, never what runs.
2. Add a `test_wrapped_command_preserves_exit_status` (nonzero) and a
   `test_wrapped_command_runs_to_completion_past_cap` (side effect after the cap
   threshold) to `harness/tests/test_guard.py` — the gap this verify-pass found is
   exactly the shape a corpus test would have caught before merge.
