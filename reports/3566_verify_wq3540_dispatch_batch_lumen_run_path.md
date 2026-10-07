# wq#3566 — Adversarial verify-pass of wq#3540 (dispatch-batch bare lumen-run PATH fix, PR#231)

**Verdict: CONFIRM** — the fix resolves the reported failure, the two defensive layers it
adds are each independently sufficient, and no case was found where the change produces
wrong args, a swallowed arg, or a regression in the existing test suite.

## What was shipped (PR#231, squash-merged as lumen-bin@8214b9d)

`dispatch-batch` only, 9 insertions, 0 deletions, no new tests:

```diff
+export PATH="/home/lumen/bin:/home/lumen/.npm-global/bin:$PATH"
+LUMEN_RUN="${DISPATCH_BATCH_LUMEN_RUN:-/home/lumen/bin/lumen-run}"
...
     "lumen-run "*)
+      cmd_line="$LUMEN_RUN ${cmd_line#lumen-run }"
       eval "$cmd_line"
```

Reported bug (wq#3540): `ssh lumen@100.117.66.102 dispatch-batch 3534` → `line 51: lumen-run:
command not found` — a non-interactive SSH PATH never carries `/home/lumen/bin`, so
`default_spawn`'s `eval "$cmd_line"` (which text-evals `router.py dispatch-cmd`'s output,
always `lumen-run start ...` per `router.py:1346`/`:1464`) failed resolving the bare command.

## Probe: reproduced the bug cold, then reproduced the fix in isolation

No committed regression test exercises `default_spawn`'s success path (the existing
`tests/test_dispatch_batch.py` only drives it through `DISPATCH_BATCH_SPAWN_CMD`, which
bypasses the eval entirely, or through the refusal branch — confirmed by reading all 13
tests; none sets `DISPATCH_BATCH_ROUTER` to a stub that prints `lumen-run ...` and lets it
reach eval). The ticket's own "done" notes describe only a manual stub run, not a
re-runnable artifact. Built an independent probe at `/tmp/fworker-wq3566/probe/` (stub
`$WQ_BIN` returning `status: open` / `claimed_by: None`, stub `$ROUTER` printing
`lumen-run start --name probe-unit --timeout-stop-sec 90 -- echo hello`, stub
`DISPATCH_BATCH_LUMEN_RUN` target that echoes its args then `UNIT=probe-unit-$$`), run via
`python3 probe.py` under `PATH=/usr/bin:/bin` (no `/home/lumen/bin`) — the exact reported
condition — with `DISPATCH_BATCH_CHILD=1` to run `run_batch` synchronously.

**Pre-fix (`lumen-bin@8214b9d~1`, extracted via `git show`), same stripped PATH:**
```
line 51: lumen-run: command not found
wq#999: spawn failed — see .../999.log
[dispatch-batch] done rc=1
```
Exact reproduction of the reported symptom.

**Post-fix (current `/home/lumen/bin/dispatch-batch`), same stripped PATH, with
`DISPATCH_BATCH_LUMEN_RUN` pointed at the stub** — deliberately isolating the
`cmd_line="$LUMEN_RUN ..."` substitution from the separate `export PATH=...` widen (which
would otherwise mask a broken substitution: if the substitution were a no-op, the widened
PATH would still resolve bare `lumen-run` to the *real* binary via lookup, silently hiding
the bug behind a different mechanism):
```
wq#999 UNIT=probe-unit-<pid>
wq#999 NOT-CLAIMED unit=probe-unit-<pid> after 1s
```
No "command not found", no "spawn failed" — the stub ran with its PID baked into the
`UNIT=` line, proving `eval` genuinely executed it (not a cached/stale value) and that the
`${cmd_line#lumen-run }` prefix-strip correctly preserved every argument after `lumen-run `
verbatim (`start --name probe-unit --timeout-stop-sec 90 -- echo hello` round-tripped to
produce the UNIT line). `NOT-CLAIMED` is expected and unrelated — the stub never calls `wq
claim`, which is outside this fix's scope.

Confirmed separately (no live invocation, to avoid spawning a real unit as a side effect of
this probe): the un-overridden default, `/home/lumen/bin/lumen-run`, exists and is
executable (`test -x`), so the production fallback path is valid.

## Why the fix can't be broken by a multi-line `cmd_line` (the one shape that would break
`${cmd_line#lumen-run }`'s literal-prefix match)

`router.py`'s `dispatch-cmd` subcommand (`router.py:1464-1467`) always does
`print(" ".join(shlex.quote(c) for c in cmd))` — a single `print`, single line, and `cmd[0]`
is always the literal string `"lumen-run"` (`router.py:1346`, and `decomposer_cmd` at
`:1382` is unreachable from this subcommand). `shlex.quote("lumen-run")` never adds quoting
(no shell metacharacters), so `cmd_line` is guaranteed to start with the literal substring
`lumen-run ` every time `dispatch-cmd` succeeds. The `case "$last_line" in "lumen-run "*)`
guard and the prefix-strip are checking/stripping the same guaranteed-single-line string, so
there's no daylight between them for a malformed id or routing variant to exploit.

## Other things tried that didn't break it

- Full existing suite: `pytest tests/test_dispatch_batch.py -q` → 1 failed (timing flake in
  `test_returns_immediately_even_though_workers_keep_running`, a `DISPATCH_BATCH_SPAWN_CMD`
  test that never touches `default_spawn`/`eval`), 10 passed; re-run in isolation → passed.
  Not a regression from this PR — unrelated code path, confirmed flaky-not-broken by
  isolation re-run.
- `DISPATCH_BATCH_LUMEN_RUN` set to empty string (not just unset): bash `${VAR:-default}`
  treats empty the same as unset, falls back to the real default path. No bug.
- FIRE_ARCHETYPE / `env`-wrapped inner commands (`router.py:1358-1368`): both wrap `inner`,
  appended *after* `cmd[0]="lumen-run"` — the outer prefix is untouched either way.

## Gap noted, not a contradiction

No regression test was added for the actual bug (the eval/PATH path) — the PR's 9-line diff
has 0 test lines, and the pre-existing suite structurally cannot reach `default_spawn`'s
success branch (see Probe section). The fix itself is correct and reproduced; the coverage
gap is a process note, not grounds to contradict. Not escalating since this isn't a
functional disagreement with the shipped diff.

## Verdict

CONFIRM. wq#3540's diff does what it says: resolves the reported `lumen-run: command not
found` under non-interactive SSH, the substitution mechanism is correct in isolation (not
just "happens to work because PATH also got widened"), and the defensive PATH widen is a
harmless, independently-sufficient second layer. No Frank-gate, no escalation.
