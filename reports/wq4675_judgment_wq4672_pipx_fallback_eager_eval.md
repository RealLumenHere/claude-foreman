# wq#4675 — judgment on the verify-pass CONTRADICT of wq#4672 (stage4c pipx install path)

Verifier: build worker wq4675-a1 (claude-sonnet-5@high, fresh instance), 2026-10-27.
Target: PR #2480 (merged `de48a72108`), `limpet/iso-builder/build.sh`
`stage4c_build_pip_tools`. Not re-implemented — read cold, probed, independently re-run.

**Verdict: CONTRADICT.** The shipped fix does not reliably put pipx binaries in
`/usr/local/bin`. Its stated root cause is almost certainly wrong, and the fallback it
adds as a safety net is itself broken by a classic nested-quoting defect that makes the
fallback's trigger condition evaluate on the wrong machine, at the wrong time, against
the wrong directory.

## What wq#4672 claimed

> Bug 1: pipx ignores inline `PIPX_HOME`/`PIPX_BIN_DIR` env vars. Fix: export them
> before `pipx install`. Also add fallback copy from `~/.local/bin/` to `/usr/local/bin/`
> in case pipx still uses the default.

## Claim 1 checked: "pipx ignores inline env vars" — does not hold

The pre-fix line (still visible at `e3ae3eb3:limpet/iso-builder/build.sh:438`) was:

```
PIPX_HOME=/usr/local/share/pipx PIPX_BIN_DIR=/usr/local/bin pipx install /tmp/dl/*
```

This is a single simple command with a leading variable-assignment prefix — POSIX shell
semantics set those variables in the *environment of that one command's process*,
identically to `export VAR=val` immediately before it. There is no shell in which
`VAR=val cmd` and `export VAR=val; cmd` produce a different environment for `cmd`'s own
process (the distinction is only about whether the assignment persists in the *calling*
shell afterward — irrelevant here, since `pipx install` is the only thing that needs
these vars on that line). Nothing between the old line and the fix changes this. The
diff's own "Bug 1" diagnosis is not a real bug; `pipx` always saw both variables, before
and after this patch. (Could not confirm pipx's actual runtime behavior against a live
container — `docker run` is denied to this worker by the house guard hook, "docker
except ps/logs" — but the claimed defect is in bash's env-propagation, not in pipx, and
that part is independently verifiable without docker, see above.)

## Claim 2 checked: the fallback copy — concrete, demonstrated defect

The new fallback (`build.sh:442-448`, current tree):

```bash
      export PIPX_HOME=/usr/local/share/pipx
      export PIPX_BIN_DIR=/usr/local/bin
      pipx install /tmp/dl/*
      # Fallback: if pipx put binaries in ~/.local/bin despite env vars, copy them
      if [ -d ~/.local/bin ] && [ -n "$(ls ~/.local/bin/ 2>/dev/null)" ]; then
        cp -a ~/.local/bin/* /usr/local/bin/ 2>/dev/null || true
      fi
```

is one line inside a *single big double-quoted string* that is `build.sh`'s own
argument to `docker run ... bash -euc "<this whole block>"` (the string opens at
`build.sh:434` and closes at `build.sh:449`). Everything in that string is parsed by
the **outer** `build.sh` shell before `docker run` ever executes — `$(...)` command
substitution is evaluated inside double quotes regardless of intent, so anything meant
to run *inside the container* has to be escaped (`\$(...)`) to defer it, exactly as the
same function already does two lines above it:

```
got=\"\$(sha256sum /tmp/dl/* | awk '{print \$1}' | head -1)\"          # build.sh:440, escaped — correct
[ -n "$(ls ~/.local/bin/ 2>/dev/null)" ]                                 # build.sh:446, NOT escaped — bug
```

Line 446's `$(ls ~/.local/bin/ 2>/dev/null)` has no backslash, so it is evaluated by
**build.sh's own shell**, on the Lumen build host, at the moment the `docker run`
command line is assembled — before the container starts, before `pipx install` runs,
before anything happens inside the sandbox this fallback is supposed to be checking.
`~/.local/bin` there resolves to the *build host's* home directory, not the
container's `/root/.local/bin` (the `[ -d ~/.local/bin ]` test on the line above is
fine — it contains no `$`, so it correctly stays literal text for the inner shell to
re-parse and tilde-expand against the container's own `$HOME` — but the `$(...)` test
right next to it does not get that same deferral).

**Reproduced directly** (no docker needed — this is purely about which shell evaluates
`$(...)` inside a double-quoted string, the same mechanism `build.sh` already relies on
correctly two lines up):

```
$ python3 -I /tmp/fworker-wq4675/repro.py
--- correctly escaped (as build.sh:440-441 does for $got) ---
literal string that would be handed to the inner shell: got="$(sha256sum nonexistent 2>/dev/null)"

--- build.sh:446 pattern, AS WRITTEN IN THE FIX (unescaped $(...)) ---
literal string that would be handed to docker run bash -euc: if [ -d ~/.local/bin ] && [ -n "HOST_SIDE_EVIDENCE_FILE" ]; then echo would-copy; fi
```

(`repro.py` builds the exact same construct `build.sh` builds — a double-quoted outer
string containing one escaped `\$(...)` and one unescaped `$(...)` — against a
`HOME_SIM` directory seeded with a marker file `HOST_SIDE_EVIDENCE_FILE`. The escaped
form stays literal text, ready for the inner shell, exactly matching the real
`got=...` line. The unescaped form already contains the marker file's name, proving the
`ls` ran immediately, on the outer host, against a directory that has nothing to do
with the container pipx will later run in.)

Consequence for the real pipeline: on an actual Lumen build host, the build user's own
`~/.local/bin` is either empty (the common case — nothing routes pipx installs there)
or contains unrelated files left by something else entirely. Either way the `-n "..."`
test is checking build-host state, frozen at string-construction time, instead of
"did pipx, which hasn't even run yet, write to the container's home dir." If the
build host's `~/.local/bin` is empty (likely), the fallback's `if` is always false and
`cp -a ~/.local/bin/* /usr/local/bin/` is dead code that never executes, in the
container or anywhere else.

## Net effect

- Claim 1's fix (export vs. inline-prefix) changes nothing pipx actually sees.
- Claim 2's fix (the fallback copy) is gated on the wrong filesystem, at the wrong
  time, so in the common case it never runs.
- If the original symptom (`verify_re_tools` finds nothing in `/usr/local/bin`) was
  real, this diff most likely does not resolve it. Nothing in the shipped change
  demonstrates otherwise: the commit touched only `build.sh` (1 file, 8 lines,
  `git show --stat de48a72108`) — no test, no probe, no build run cited in the PR or
  in wq#4672's done-notes. `limpet/iso-builder/test_stage4c_pip_paths.py` (the one
  existing test that touches this function) cannot catch this: its fake `docker` drops
  a dummy binary straight into the bind-mounted `/usr/local/bin` unconditionally,
  without ever parsing or executing the generated `bash -euc` script string, so the
  escaping bug — or any bug inside that string — has no path to make the test fail
  (wq#2630: a probe that can't fail isn't a probe).

## Disposition

Did not re-implement (ticket instruction: verify, don't fix). Filed **wq#4676** (lane
build, pri 2, deps wq#4675, claude-opus-5@high per archetype 8's contradiction
escalation) with this fix spec:

1. Escape the fallback's command substitution: `\$(ls ~/.local/bin/ 2>/dev/null)` (and
   re-audit the two other bare `~/.local/bin` occurrences on the same lines — they are
   currently correct only because they contain no `$`, which is easy to break by
   accident in a future edit).
2. Re-examine whether `PIPX_BIN_DIR` is actually being honored by the `pipx` version
   `apt-get install`s inside the `ubuntu:$XUBUNTU_VERSION` container — if it is, the
   fallback is unnecessary belt-and-suspenders and the real fix is just confirming
   that; if it isn't, the fallback needs to work, which means fixing (1).
3. Add a test that actually exercises the generated script string (fakes `pipx` as a
   binary that writes to `$HOME/.local/bin` instead of respecting `PIPX_BIN_DIR`,
   runs the real inner script through `bash -c`, and asserts the binary lands in the
   bind-mounted `/usr/local/bin`) rather than one that fakes `docker` into bypassing
   the script entirely.
4. Liveness tag: `bash -n limpet/iso-builder/build.sh && pytest
   limpet/iso-builder/test_stage4c_pip_paths.py -v`, plus the new test from (3).

Logged as **kb/ISSUE-LOG.md row 1592**.

## Scope note

`docker run` (and anything but `docker ps`/`docker logs`) is on this worker's deny-list
(house guard hook), so the actual `pipx`-inside-`ubuntu:$XUBUNTU_VERSION` runtime
behavior could not be exercised directly from this sandbox. The CONTRADICT above does
not depend on that: it rests on (a) textbook POSIX env-propagation semantics for Claim
1, and (b) a directly reproduced, docker-independent demonstration of the quoting bug
for Claim 2. Naming the refused shape here per the ticket's own instruction, rather than
burning attempts working around it.
