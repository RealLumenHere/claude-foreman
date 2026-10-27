# wq#4676 — stage4c pipx fallback: fix the host-side evaluation, and test the generated script

Worker: build worker wq4676-a1 (claude-opus-5@high), 2026-10-27.
Target: `limpet/iso-builder/build.sh` `stage4c_build_pip_tools`, per wq#4675's CONTRADICT
(`reports/wq4675_judgment_wq4672_pipx_fallback_eager_eval.md`, kb row 1592).
Scope held to the escaping bug and its missing coverage — wq#4672's diff was not
re-implemented.

## (a) The escaping fix — `build.sh:453`

```
-      if [ -d ~/.local/bin ] && [ -n "$(ls ~/.local/bin/ 2>/dev/null)" ]; then
+      if [ -d ~/.local/bin ] && [ -n \"\$(ls ~/.local/bin/ 2>/dev/null)\" ]; then
```

wq#4675 was right that the `$(` had to be escaped. Two corrections to its severity read,
both demonstrated:

1. **The bare `"` mattered as much as the bare `$`.** The whole block is a single
   double-quoted string that is build.sh's own argument to `docker run ... bash -euc`, so
   the unescaped quotes *closed that string*. The substitution ran unquoted between two
   concatenated quoted fragments, which means its output also **word-split into docker's
   argv**. On a build host whose `~/.local/bin` holds 2+ entries, `bash -euc` therefore
   received a script truncated mid-`if`, with the rest of the block arriving as positional
   parameters. Observed, with the pre-fix line restored:

   ```
   stage4c: pipx install footool==1.0.0 (container ubuntu:26.04, sha256-pinned)
   fake-docker: -c: line 21: syntax error: unexpected end of file from `if' command on line 20
   ```

   `build.sh:19` is `set -euo pipefail`, so that is not latent dead code — it aborts the
   ISO build at stage4c on any such host.

2. **On an empty host `~/.local/bin` the guard was not "always false".** It collapsed to
   `[ -n  ]`, a *one-argument* test, which bash evaluates TRUE (`-n` is itself a non-empty
   string):

   ```
   $ python3 -I /tmp/fworker-wq4676/empty_home_guard.py
   '[ -n  ]'    -> TRUE  rc=0
   '[ -n "" ]'  -> FALSE  rc=0
   ```

   So on an empty-home host the `cp -a ~/.local/bin/* /usr/local/bin/ 2>/dev/null || true`
   ran unconditionally and harmlessly. The CONTRADICT verdict stands either way: the
   fallback never tested the thing it claims to test.

`~` and `*` on the surrounding lines need no escaping (no tilde or glob expansion happens
inside double quotes), and a comment at `build.sh:445-452` now says so, since those lines
are one stray `$` away from the same defect.

## (b) Does the container's pipx honour `PIPX_BIN_DIR`?

Documented as yes. pipx's own "Configure paths" reference says pipx splits its files
across four locations, "each with an environment-variable override and a `--global`
counterpart ... Set any variable to override its location"
(https://pipx.pypa.io/latest/how-to/configure-paths.html). Combined with wq#4675's claim-1
finding (`VAR=val cmd` and `export VAR=val; cmd` give `cmd`'s process an identical
environment, so wq#4672's `export` change was a no-op), the env-var plumbing was correct
before and after wq#4672 — the fallback is belt-and-suspenders, not the load-bearing path.
It is kept, now actually functional, because the original wq#4672 symptom (nothing in
`/usr/local/bin`) was never root-caused to a reproduced cause.

Not confirmed against a live `ubuntu:26.04` container: `docker run` is on this worker's
guard-hook deny-list ("docker except ps/logs"). Naming the refused shape per the ticket's
instruction rather than burning attempts around it. If `PIPX_BIN_DIR` turns out to be
honoured in that image, the right follow-up is to delete the fallback, not to grow it.

## (c) A test that can fail

`limpet/iso-builder/test_stage4c_pip_paths.py` gains
`test_stage4c_pipx_fallback_runs_inside_the_container_not_on_the_build_host` plus a
`bash -n build.sh` parse check. The old fake `docker` dropped a dummy binary straight into
the bind-mount and never looked at the generated script, so no defect inside that string
could fail a test (wq#2630). The new `FAKE_DOCKER_EXEC` **executes** it: container absolute
paths are rewritten to their bind-mount sources, `$HOME` points at a per-invocation
stand-in container home, `apt-get`/`pip`/`sha256sum` are faked on PATH, and **`pipx` is
faked as a pipx that ignores `PIPX_BIN_DIR`** and writes to `$HOME/.local/bin` — so the
fallback copy is the only route to the bind-mounted `/usr/local/bin`. bash itself is real.

The probe is drawn from outside the population the fix was written around (wq#2630): the
host-side `$HOME/.local/bin` is seeded with *two* marker files, the case wq#4672 never
considered, and the test asserts the generated script still contains the literal
`$(ls ~/.local/bin/ 2>/dev/null)`, contains no `host-marker` name, arrives as exactly
`bash -euc <script>` with nothing spilled past it, and that no host marker reaches the
image. Inputs are tmp_path fixtures, so the probe is re-runnable.

Falsifiability, run both ways:

```
$ python3 -m pytest limpet/iso-builder/test_stage4c_pip_paths.py -q
3 passed in 0.55s
# with build.sh:453 reverted to the bare-$( form:
1 failed, 2 passed in 0.50s   (stage4c aborts: syntax error, unexpected end of file)
```
