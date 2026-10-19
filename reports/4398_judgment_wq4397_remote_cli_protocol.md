# wq#4398 — judgment on wq#4397's CONTRADICT of wq#4396 (remote-cli MCP wrapper)

opus-5@high, worktree `/home/lumen/worktrees/wq4398-a1`. Input:
`reports/4397_verify_wq4396_remote_cli_protocol_mismatch.md` (landed on `main`
as `e3530cf8`), commit `a6dff26d` (PR#2290), and the real wq#4395 deliverable
at `/home/lumen/bin/remote-cli`.

## wq#4397's technical findings: UPHELD, both of them

Re-derived independently, not taken on trust.

1. **Protocol mismatch.** `remote-cli:272` reads `req.get("cmd")`;
   `remote-cli:273-291` implements exactly five commands — `snapshot`,
   `scrollback`, `send_keys`, `resize`, `status` — and `remote-cli:292` answers
   anything else with `unknown cmd <repr>`. `a6dff26d`'s `mcp/server.py:89`
   sent `{"op": op, ...}` with command names `exec`/`snapshot`/`send`/`logs`.
   Three of the four names are wrong *and* the key is wrong, so `cmd` was
   always `None`. There is no `exec` in remote-cli at all.
2. **`exec`'s `timeout` was dead.** `tool_exec`'s `timeout` was forwarded as a
   JSON *field* (`mcp/server.py:127`) while `_call` constructed a bare
   `RemoteCliClient()` (`:114`) pinned to `DEFAULT_TIMEOUT = 5.0` (`:49`).

Side-by-side probe against a real `remote-cli serve` PTY
(`/tmp/fworker-wq4398/probe_old_vs_new.py`, both halves driving the same
daemon build):

```
--- old_a6dff26d ---
  snapshot  -> ok=False error='unknown cmd None'
  logs      -> ok=False error='unknown cmd None'
  send      -> ok=False error='unknown cmd None'
  exec(6s cmd, timeout=20) -> ok=False error='unknown cmd None' after 0.00s
--- new_wq4398 ---
  snapshot  -> ok=True
  logs      -> ok=True
  send      -> ok=True
  exec(6s cmd, timeout=20) -> ok=True exit_code=0 after 6.03s
```

0/4 → 4/4, and `exec` now returns at 6.03s under a caller-supplied 20s budget,
i.e. past the 5s ceiling that used to be unconditional.

## Judgment (1): corrective follow-up PR, not a revert

Revert is the wrong instrument here. The defect is confined to one layer of one
file: the wire encoding inside `mcp/server.py`. Everything else `a6dff26d`
shipped is correct and was independently re-checked by wq#4397 — the manifest,
the energize classification (`limpet-plugin.toml:52-65`, snapshot/logs
read-only, send/exec gated, with `exec` gated on the author's own judgment
beyond what the ticket named), install/uninstall/health scripts, the plugin
layout matching `limpet-plugin-flashrom`, and the JSON-RPC surface. A revert
would discard all of that to fix one encoding bug, and `main` already carries a
dependent docs commit (`e3530cf8`). Corrective PR: this one.

## Judgment (2): `exec` stays, synthesized client-side from `send_keys` + polled `scrollback`

The choice was "redesign `exec` on the primitives that exist" vs. "extend
wq#4395's protocol with a real synchronous exec". The second option is a
mirage: remote-cli's whole purpose is that a human may be attached to the same
PTY, so running a command *in that session* means typing it into the PTY —
there is no second channel. A server-side `exec` would have to do the same
marker-and-poll trick internally, just in a binary already deployed to Lumen
and Maksoor (wq#4395 notes) and therefore more expensive to change and fix.
Keeping the heuristic in the MCP wrapper puts it in the layer that can be
corrected without a fleet redeploy, and leaves remote-cli's five-command
protocol minimal.

So `exec` now: type `<command>; __rc_status=$?; printf '%s%s-%s-%s\n' RC DONE
<token> "$__rc_status"`, then poll `scrollback` until `RCDONE-<token>-<status>`
appears, parse the exit code, carve the output between the echoed payload line
and the marker. Prior art for the shape:
`deepseek-ai/dsh-tool-bash-persistent` ("each command is wrapped with unique
start/end markers carrying the exit status; the tool polls the PTY scrollback
and extracts…") — the same problem, the same answer.

One non-obvious detail that a naive implementation gets wrong: the marker is
assembled by `printf` from separate arguments, so the shell's **echo** of the
payload contains `RC DONE <token>` (spaces) while only `printf`'s output
contains `RCDONE-<token>-<status>`. Without that split, the first poll matches
the echo instantly and reports the *previous* command's exit code.
`tests/test_server.py::test_exec_payload_echo_cannot_match_its_own_marker`
locks that in.

Limits are documented on `tool_exec` and in the README rather than hidden: the
session must be a shell at a prompt (if a human left it in `vim`, `exec` types
into `vim` and times out), `command` is one shell line, and `output` is raw
scrollback including `\r\n` and escapes.

## Judgment (3): `timeout` fixed by separating two different clocks

`REQUEST_TIMEOUT = 5.0` now bounds **one** socket round-trip, which is all any
remote-cli command is (read a buffer / write a PTY fd). `tool_exec`'s `timeout`
is a wall-clock **deadline** spanning many such round-trips, and each one's
socket timeout is `min(REQUEST_TIMEOUT, deadline - now)` so a short caller
budget is never overshot by a single `recv`. `_call` passes `timeout` to
`RemoteCliClient` instead of onto the wire. `DEFAULT_TIMEOUT` is kept as an
alias. Conflating the two clocks is exactly what produced the original bug.

## Judgment (4): the process question — the escalation's premise is wrong, and the gate it asks for already exists

wq#4398's text (inherited from wq#4397) says wq#4396 "was dispatched and merged
against wq#4395 (deps:4395)". That is not what the queue records.

- `wq show 4396` → **`deps` is empty.** There was no dependency clause.
- `deps_met()` (`/home/lumen/bin/wq:1065-1070`) already requires every dep to
  be `status == 'done'`, and claim is refused when it is not. The hard gate the
  ticket asks for is already implemented and already strict.
- Timeline: 4395 created `20:45:26`, 4396 created `20:45:27`; 4395 claimed
  `20:47:37`, 4396 claimed `20:47:41`. The two were created one second apart
  and dispatched four seconds apart — fanned out in parallel, with the
  dependency expressed only in 4396's prose ("remote-cli as Limpet shared
  terminal backend").

So the failure was **not** an unenforced dependency. It was an *unrecorded*
one: a ticket whose entire deliverable is a wrapper around another ticket's
artifact was created with nothing for `deps_met()` to check. Adding a
"deps must be done" gate would have changed nothing on 4396. This is itself a
Tier-0-instinct-8 shape one level up — `deps:4395` was a value introduced into
the escalation's own diagnosis and never checked against the queue, and it
would have sent the fix at the wrong mechanism.

The actionable guard is at creation, not at claim: when a ticket's
title/detail names another `wq#NNNN` as the artifact it builds on and `deps` is
empty, `wq add` should refuse or warn — and must *not* fire on a back-reference
to an already-`done` ticket, which is common in detail prose. Filed as
**wq#4402** against `/home/lumen/bin/wq`, which lives in lumen-bin and is out
of this worktree's scope. ISSUE-LOG row 1473 appended.

## Judgment (5, not asked but the load-bearing one): the test suite was the real defect

`a6dff26d` shipped 8/8 green with 0/4 tools working because
`tests/test_server.py`'s fake dispatched on `request.get("op")` — the author's
own invented key, written by the author. A suite that can only confirm the
assumption it was built from is not coverage. Fixed in three layers:

1. **Committed protocol fixture** — `tests/fixtures/remote_cli_protocol.json`
   pins the `cmd` key, the five command names and their fields, with
   provenance (`remote-cli:9-18`, `:271-292`, read 2026-10-19). Immutable and
   re-runnable, per the acceptance-probe doctrine; tests assert the wrapper
   puts nothing else on the wire.
2. **A fake that speaks the real protocol** over a real Unix socket, rejecting
   unknown `cmd` exactly as `remote-cli:292` does — the remedy shape
   ISSUE-LOG row 1309 (wq#3881, OpenOCD MCP) already established.
3. **A live layer** that spawns the actual `remote-cli` binary over a real PTY
   (`REMOTE_CLI_BIN`, skips if absent) and drives all four tools end to end —
   the probe wq#4396 never ran despite the binary existing on disk 26s before
   its commit. Plus a drift guard asserting the binary's own `cmd ==` literals
   still equal the fixture, so a future wq#4395 protocol change fails a test
   instead of silently re-breaking the wrapper.

### The probe can fail

Required by the acceptance-probe doctrine, and demonstrated rather than
asserted. The new suite run unchanged against `a6dff26d`'s `server.py`
(`/tmp/fworker-wq4398/falsify/`, the old file + the new tests):

```
18 failed, 8 passed
```

Every live test and every protocol-conformance test fails; the 8 that pass are
the JSON-RPC shape tests, which the old file genuinely got right. Against the
corrected file, in-tree: **26 passed, 0 skipped** (the live layer ran — the
real binary is present on Lumen).

Boundary inputs deliberately outside the defaults the fix was designed around:
a 6s command under a 20s caller timeout (both outside the old 5s ceiling *and*
outside `REQUEST_TIMEOUT`), a 2s caller timeout against `sleep 30` (below the
5s floor the old code would have imposed), and a socket path in `tmp` rather
than the default `/run/remote-cli/shared.sock`.

## What is deliberately not in this PR

- No change to `/home/lumen/bin/remote-cli`. Its protocol is the contract; it
  is deployed on two hosts and was not at fault.
- No change to `[mcp.tools_energize]` or the MCP tool names. Those are this
  plugin's API and wq#4397 confirmed the classification is self-consistent; the
  bug was entirely beneath them.
- No new tools (`resize`/`status` stay unexposed) — out of scope, and each
  would need an energize ruling of its own.
- No `wq add` guard. Different repo (lumen-bin), filed as wq#4402.
