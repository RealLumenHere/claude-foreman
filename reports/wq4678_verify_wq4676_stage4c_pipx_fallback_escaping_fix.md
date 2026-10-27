# wq#4678 — verify-pass on wq#4676 (stage4c pipx fallback escaping fix)

Verifier: build worker wq4678-a1 (claude-sonnet-5@high, fresh instance), 2026-10-27.
Target: commit `d2f4e867` (`limpet/iso-builder/build.sh` + `test_stage4c_pip_paths.py`),
shipped by wq#4676 against wq#4675's CONTRADICT of wq#4672
(`reports/wq4675_judgment_wq4672_pipx_fallback_eager_eval.md`, kb row 1592). Read cold,
not re-implemented.

## What wq#4676 claimed

1. The escaping fix at `build.sh:453` — both the bare `$(` **and** the bare `"` inside
   wq#4672's fallback guard needed escaping, not just the `$(` wq#4675 flagged. The bare
   `"` closed build.sh's own double-quoted `bash -euc "..."` string early, so the
   substitution's output word-split into `docker run`'s argv.
2. On an empty build-host `~/.local/bin`, the pre-fix guard collapsed to the one-argument
   test `[ -n ]`, which bash evaluates **true** — so wq#4675's "always false / dead code"
   characterization of the empty-host case was itself wrong; the real failure mode was
   worse (unconditional stage4c abort on hosts with 2+ entries in `~/.local/bin`).
3. A new test, `test_stage4c_pipx_fallback_runs_inside_the_container_not_on_the_build_host`,
   actually executes the generated `bash -euc` script (rather than bypassing it with a
   stub `docker`) and is falsifiable: it fails against the pre-fix line, passes against
   the fix.

## Checks performed

**Escaping fix, read in full file context** (`build.sh:413-462`, not just the diff hunk):
confirmed `$package`/`$ver`/`$sha256` are correctly *un*escaped (outer-shell values meant
to be interpolated per loop iteration), `\$got`/`\$(sha256sum...)` on lines 440-441 and the
new `\$(ls ...)`/`\"..\"` on line 453 are correctly escaped (inner-shell values deferred
past string assembly), and the new explanatory comment (453±) contains no unescaped `$`/`"`
that would corrupt the outer string. No other bare `$(...)` or bare `"` exists inside the
`bash -euc "..."` block (`grep -nE '\$|"'` over lines 434-456) — the fix is complete, not
spot-patched.

**Claim 2, independently reproduced** without relying on the report's own script:
```
$ [ -n ]; echo "rc=$?"
rc=0
```
Confirms `[ -n ]` is true — matches wq#4676's correction of wq#4675's severity read.

**Claim 3 (probe falsifiability), re-run both ways** — this is the load-bearing check per
wq#2630 (a probe that can't fail isn't a probe):
```
$ python3 -I -m pytest limpet/iso-builder/test_stage4c_pip_paths.py -v
3 passed in 0.55s
```
Then reverted `build.sh:453` to the exact pre-fix bare-`$(`/bare-`"` line (saved a backup
first, restored after) and re-ran:
```
$ python3 -I -m pytest limpet/iso-builder/test_stage4c_pip_paths.py -v
...
AssertionError: [..] stage4c: pipx install footool==1.0.0 (container ubuntu:26.04, sha256-pinned)
  fake-docker: -c: line 21: syntax error: unexpected end of file from `if' command on line 20
1 failed, 2 passed
```
Matches the report's cited failure mode exactly (truncated script, syntax error at the
`if`). Restored the fix afterward; `git diff --stat` on `build.sh` confirmed a clean
restore (no residual diff).

**Regression sweep**: `bash -n limpet/iso-builder/build.sh` parses clean; full
`limpet/iso-builder/` suite (not just the touched test file) — `162 passed in 44.92s`, no
collateral breakage from the fix or the new test's fakes.

**kb/ISSUE-LOG.md row 1592**: already updated to `resolved by wq#4676`, with the same two
corrections (bare-`"` severity, empty-host `[ -n ]` true) recorded — consistent with the
PR, not a mismatched claim.

## Attempted breaks (none landed)

- Tried to find an unescaped `$`/`"` elsewhere in the block the fix didn't touch — none
  found (see grep above).
- Checked whether the new comment itself (added inside the same double-quoted string)
  introduces a fresh unescaped `$`/`"` — it doesn't; both occurrences in the comment text
  are escaped.
- Checked whether `~` or `*` on the surrounding lines needed escaping now that the `if`
  line was touched — they don't (no tilde/glob expansion inside double quotes either
  shell), and the fix's own comment says so; verified by inspection, not just taken on
  faith.
- Considered whether the new test's `FAKE_DOCKER_EXEC` harness could pass for the wrong
  reason (e.g. masking the bug via its own string handling) — ruled out by the revert
  test above: it fails for the documented reason (syntax error from the truncated `if`),
  not a harness artifact.

## Verdict: CONFIRM

wq#4676's diff is correct, complete, and its new test is a real probe (demonstrated
falsifiable in both directions). No contradiction found. Not a judgment trigger — closing
without escalation.
