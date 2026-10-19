# wq#4403 verify-pass of wq#4398 (PR#2295) — CONTRADICT

Ticket: wq#4403, archetype verify-pass, sonnet-5@high, fresh instance.
Target: wq#4398's corrective PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2295
(merged to main as `bc235d80`, equivalent content to the PR's `76ab295f8c`),
which rewrote `plugins/limpet-plugin-remote-cli/mcp/server.py` to fix wq#4397's
two confirmed defects (invented `op`/exec/snapshot/send/logs wire protocol vs.
remote-cli's real `cmd` protocol; `exec`'s `timeout` kwarg never reaching the
socket).

## What I verified and confirms

1. **Wire protocol now matches the real daemon.** Read `/home/lumen/bin/remote-cli`
   directly (lines 271-292, `_handle_control`): dispatch is on `req.get("cmd")`
   over exactly `snapshot`/`scrollback`/`send_keys`/`resize`/`status`. The
   shipped `server.py` (`mcp/server.py:134-176`, `_call`/`RemoteCliClient.request`)
   sends exactly this five-command set and nothing else — confirmed by reading
   the diff and by `tests/fixtures/remote_cli_protocol.json`'s `_provenance`
   pinning the same source lines.
2. **In-tree suite passes as claimed.** `python3 -m pytest tests/test_server.py -q`
   → `26 passed in 13.86s` (run from
   `/home/lumen/worktrees/wq4403-a1/plugins/limpet-plugin-remote-cli`), including
   the live layer against the real `/home/lumen/bin/remote-cli` binary
   (`needs_remote_cli` tests, e.g. `test_live_exec_honors_a_timeout_past_the_old_five_second_cap`).
3. **Falsifiability claim reproduced exactly.** Swapped `mcp/server.py` for the
   pre-fix version at `a6dff26d` (same test file, same fixtures), reran the
   suite: `18 failed, 8 passed` — matches the PR's claimed numbers verbatim.
   Restored the fixed file afterward; `git status` on the file is clean.
4. **Timeout fix confirmed live**, independent of the shipped tests: the real
   daemon's `scrollback_tail(0)` (`remote-cli:242-247`) returns the full buffer,
   matching `server.py`'s use of `lines=0` for the final exit-code read; the two
   clocks (`REQUEST_TIMEOUT` for one round-trip vs. `tool_exec`'s wall-clock
   `deadline`) are correctly separated at `mcp/server.py:219-221`.

wq#4397's two original findings are genuinely fixed. The PR's claims about them
hold up.

## What breaks: `tool_exec`'s `output` field is wrong under two realistic,
## non-contrived conditions neither the unit nor the live test layer exercises

`_exec_output` (`mcp/server.py:199-216`) assumes the completion token appears
**exactly twice** in scrollback: once in the shell's echo of the typed payload
line, once in printf's marker output — "everything between the end of the echo
line and the start of the marker is what the command printed." That assumption
is false whenever the payload line's own echo does not immediately precede the
command's real output, which happens in two realistic cases:

### A. Embedded newlines (a case the module's own docstring claims to support)

`tool_exec`'s docstring (`mcp/server.py:237-238`): "`command` is one shell
line; a trailing newline is stripped. Embedded newlines run as successive
lines and `exit_code` is the last one's." This implies the whole multi-line
command genuinely runs — it says nothing about `output` being truncated.

Live repro (real `remote-cli serve` + `/bin/bash`, probe script below):

```
result = server.tool_exec("echo FIRSTLINE\necho SECONDLINE\necho THIRDLINE", timeout=10.0)
→ {'ok': True, 'exit_code': 0, 'output': 'THIRDLINE'}
```

Raw scrollback captured in the same run shows `FIRSTLINE` and `SECONDLINE`
really printed:

```
'bash-5.3$ echo FIRSTLINE\r\nFIRSTLINE\r\nbash-5.3$ echo SECONDLINE\r\nSECONDLINE\r\n
 bash-5.3$ ...printf...\r\nTHIRDLINE\r\nRCDONE-<token>-0\r\n bash-5.3$ '
```

`_exec_output` finds the token's first occurrence at the echo of the **last**
typed line (the one carrying the marker clause) and slices away everything
before it — silently discarding `FIRSTLINE`/`SECONDLINE`'s real output.
`exit_code` is still correct (it's parsed separately, from a `marker.search`
over the un-sliced full text in `tool_exec` itself), so the tool reports
`ok: True` with a plausible-looking but incomplete `output`.

### B. Local terminal echo disabled (a normal state for a human-attended shared PTY)

If something in the shared session has turned off echo (`stty -echo`, a
password prompt, a `read -s`, anything a human attached to the *same* PTY this
tool is designed to share might leave behind — this is exactly the scenario
the module docstring cites as the reason this tool exists), the typed payload
line is never echoed, so the token appears **once**, in the marker itself, not
twice. The real command output appears **before** that single occurrence, and
`_exec_output`'s slice-after-first-occurrence logic throws it away, returning
whatever trailing text follows the marker (typically the next shell prompt)
instead.

Live repro, same session, after `server.tool_send("stty -echo\n")`:

```
result = server.tool_exec("echo hidden-echo-probe", timeout=10.0)
→ {'ok': True, 'exit_code': 0, 'output': 'bash-5.3$ '}
```

The real output (`hidden-echo-probe`) is gone; `output` is a stale prompt
string. This is silent data corruption, not an error — `ok: True` and a
correct `exit_code` make it look trustworthy.

The shipped unit test for this exact code path,
`test_exec_output_tolerates_echo_suppressed_session`
(`tests/test_server.py:266-268`), only feeds `_exec_output` a scrollback
consisting of *just* the marker line with nothing before it, so it can only
ever assert the empty-output case. It never exercises the case where real
output legitimately precedes a single-occurrence marker — which is exactly
what happens live. Same failure shape wq#4397 found in wq#4396's original
suite: a test built to the same assumption as the code it's testing cannot
catch that assumption being wrong.

### C (noted, lower confidence as a "bug" vs. a disclosed heuristic limit)

A command ending in a shell comment (`echo visible # a comment`) makes the
appended `; __rc_status=$?; printf ...` marker clause part of the comment, so
it never runs; `tool_exec` times out at the caller's full timeout with no
indication of why. Live-reproduced (`timed_out: True` at 4.0s). This follows
directly from appending the marker on the *same line* with `;`, a known sharp
edge of this marker-injection style (the alternative — put the marker on its
own line after a real newline — survives trailing comments). Flagging for the
judgment pass; weaker than A/B since the module docstring's "heuristic with
real limits" disclaimer arguably already covers unanticipated shell syntax.

## Why this is a CONTRADICT, not a nit

The PR's central claim is "4/4 tools work end to end" (old 0/4 → new 4/4) and
ships a three-layer test suite specifically to catch the class of defect
wq#4397 found (a fake built to the same wrong assumption as the code). Findings
A and B are a new instance of that exact failure mode, one layer up: the
*live* test layer only ever runs single-line commands with echo on, so it
cannot see that `_exec_output`'s two-occurrences-of-the-token assumption is
false precisely when (A) the module's own documented multi-line support is
used, or (B) the shared-PTY-with-a-human scenario the whole tool exists for
puts the session into no-echo mode. `exec` does not merely have a documented
limitation here — it silently returns wrong data while reporting `ok: True`.

## Repro artifacts

Probe scripts (not part of the repo, run from a fresh real `remote-cli serve`
+ `/bin/bash` daemon spawned by the probe itself, same pattern as
`tests/test_server.py`'s `LiveRemoteCli` fixture):
- `/tmp/fworker-wq4403/probe.py` — embedded comment, echo-suppressed, apostrophe
  cases.
- `/tmp/fworker-wq4403/probe2.py` — multi-line output-loss case with raw
  scrollback dump.

## Verdict

**CONTRADICT.** wq#4397's two original findings (protocol mismatch, timeout)
are genuinely fixed and the PR's falsifiability/live-probe claims about them
reproduce exactly. But the corrective PR's own new `_exec_output` logic has an
independent, live-reproduced correctness bug: `exec`'s `output` field is
silently wrong (not erroring) for multi-line commands and for any session with
local echo disabled, both of which are states the shipped diff's own docstring
and design rationale say `exec` should handle. Escalating per ticket
instructions — follow-up judgment ticket filed referencing this report.
