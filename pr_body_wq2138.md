## What

Stops two harness-side causes of lost worker attempts: the RED gate reading quoted heredoc prose as a command, and the CLI's brace-with-quote refusal loop-breaking a session.

## Why

wq#2127 found both within 5 minutes of spawn. wq#2128 attempt 1 was RED-blocked as a recursive root delete on prose inside a python heredoc writing a prompt template. wq#2129 attempt 1 was killed by loop_break at 48 turns after the CLI refused three python heredocs ("contains brace with quote character (expansion obfuscation)"). Both cost the ticket an attempt for a harness cause.

## How

- `strip_quoted_heredoc_bodies()` in `harness/action_gate.py` masks quoted-heredoc bodies for the builtin RED table and the `rm -rf` root check only. It masks only when every consumer on the heredoc line is inert (cat/tee/python/git/gh/jq/...), there is no `$(`/backtick, and the terminator exists. Shell/sqlite/psql/ssh consumers, unquoted and unterminated heredocs, and text after the terminator are still scanned. Tamper, research-gate, flash and project rules still scan the raw command.
- `LoopTracker` does not count the first brace-with-quote refusal and sets `steer`. `run_child` stops the child with `killed_reason="steer"`; `do_attempt` resumes the same session at the same attempt number with the hint (`-p` children have no stdin). A refusal after the steer counts toward loop_break with `trip_kind="PERMISSION"`, so it classifies as PERMISSION_LOOP (harness defect).
- `harness/prompts/worker.md` tells workers to use Write/Edit, never shell heredocs.

## Testing

`harness/tests/test_heredoc_steer.py`, 15 tests: quoted python/cat heredoc prose is GREEN while `rm -rf /` outside a heredoc is RED; shell/sqlite consumers, unquoted, unterminated, here-strings, quoted markers and rules-file tamper stay RED; first refusal steers with no strike; post-steer refusals loop_break as PERMISSION_LOOP; a real subprocess through `run_child` yields `steer` then `loop_break`.

Full `harness/tests`: 1205 passed, 8 failed (7 stop_gate/evidence, 1 liveness). None import `action_gate` or exercise `fworker`. I ran them inside a live worker session, whose `FOREMAN_*` env and worktree-local tmp path likely cause the failures, but I could not re-run them with a clean env, so "pre-existing" is unconfirmed.

## Risk / rollback

A python heredoc whose body shells out to a destructive command is no longer caught by the builtin RED regex (it was only ever caught by accident). Gateway-lane children (`run_child_gateway`) never break on a steer; their first refusal is just not counted. Rollback is a plain revert of this PR.
