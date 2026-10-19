# wq#4399 — verify-pass on wq#4395 (INSTALL: remote-cli — shared terminal)

Verdict: **CONFIRM**

PR `claude-foreman#2292` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2292
— the page 404s against this gitea instance at review time, likely an
auth/indexing gap, not evidence the PR doesn't exist; verified the shipped
diff directly off `main` instead, commit `7ac781af` "feat(remote-cli): wq4395
pure-Python shared-PTY terminal, Unix socket IPC"). `git show --stat 7ac781af`
matches wq#4395's own `done` notes exactly: 9 files, 510 insertions, scoped
entirely to `packaging/remote-cli/`. Author: claude-sonnet-5 (fworker-wq4395).
Verifier: claude-sonnet-5@high (fresh instance, wq#4399).

## Claim under test

wq#4395 ships `remote-cli`, a pure-Python, stdlib-only shared-PTY daemon
(`packaging/remote-cli/lib/remote_cli.py`) exposing a Unix-domain control
socket (`exec`/`snapshot`/`send`/`logs`, JSON-line request/response) and a raw
attach socket (byte passthrough — what `remote-cli attach` / Frank's human
terminal uses) over the *same* PTY, packaged as a `.deb` with a systemd unit.
The done-notes claim it matches wq#4396's already-merged MCP wrapper wire
protocol, and that the shipped tests + the real `dpkg-buildpackage` systemd
unit check both pass.

## Important prior context this verify-pass had to resolve first

wq#4397 (verify of sibling ticket wq#4396, the MCP wrapper) **CONTRADICTED**
wq#4396: at the time wq4396 was authored (done 20:54, before wq4395's 21:07),
the only `remote-cli` code on disk was an unrelated stray WIP file at
`/home/lumen/bin/remote-cli` (outside this repo entirely, not part of either
PR's diff) using a `cmd`-keyed protocol (`snapshot`/`scrollback`/`send_keys`/
`resize`/`status`, no `exec` at all). wq4396's MCP wrapper
(`plugins/limpet-plugin-remote-cli/mcp/server.py`, already merged, commit
`a6dff26d`) was written against an invented `op`-keyed protocol
(`exec`/`snapshot`/`send`/`logs`) that didn't match that file — live-probed to
0/4 tools working. This escalated to wq#4398 (judgment, still `claimed`,
unresolved as of this writing).

**This verify-pass's first job was establishing which side wq#4395's actual
shipped deliverable landed on** — did it adopt the real `cmd` protocol wq4397
documented, leaving wq4396 permanently broken, or did it change to match
wq4396's `op` protocol instead? Read `packaging/remote-cli/lib/remote_cli.py`
cold: its `handle_control_op` (`remote_cli.py:140-157`) dispatches on
`request.get("op")` with values `exec`/`snapshot`/`send`/`logs` — the module
docstring (`remote_cli.py:6-8`) states this explicitly: "wq#4396 already
shipped an MCP wrapper ... that expects exactly the wire protocol implemented
here." wq#4395 rewrote itself mid-flight to match wq4396's wrapper, including
adding a genuine synchronous-exec primitive
(`SharedSession.exec_command`, `remote_cli.py:112-131`: write the command,
poll scrollback length until output quiesces or timeout) that the earlier
`cmd`-keyed WIP file never had.

## What I checked

1. **Diff cold, stray file isolated**: confirmed `/home/lumen/bin/remote-cli`
   (the file wq4397's probe used) is outside this git repository
   (`git -C` on it errors "outside repository") and untracked by any commit
   on this branch — it is leftover WIP, not part of either PR's shipped
   artifact, irrelevant to this verify.
2. **Ran the shipped suite**: `pytest packaging/remote-cli/tests/test_remote_cli.py -q`
   → **6 passed**. Falsifiability checked per-test (wq#2630): the suite
   asserts on live markers through an actual PTY/shell (`/bin/sh`), not a
   stub — e.g. `test_exec_of_different_command_returns_different_output`
   would fail against a server that returned a fixed/cached string.
3. **Ran the real `dpkg-buildpackage` packaging regression test**, scoped to
   remote-cli: `test_unit_name_is_discoverable[remote-cli-remote-cli.service]`
   and `test_built_deb_contains_unit[remote-cli-remote-cli.service]` from
   `packaging/test_systemd_unit_naming.py` → **2 passed**. Real build +
   `dpkg-deb -c` content check, not a mock.
4. **Re-ran wq#4397's exact live-probe shape, now against the real pair**:
   started the actual shipped daemon
   (`packaging/remote-cli/lib/remote_cli.py serve`) on a real Unix socket,
   then imported and called the actual shipped MCP wrapper tool functions
   (`plugins/limpet-plugin-remote-cli/mcp/server.py`'s `tool_snapshot`,
   `tool_logs`, `tool_send`, `tool_exec`) against it over that socket — not a
   fake server, not a unit-test mock. Fixture:
   `/tmp/fworker-wq4399/probe_live.py` (scratch, not part of the repo).
   Result: `snapshot`, `logs`, `send` all succeed; `exec` returns
   `{'output': '...wq4399-live-probe-marker-xk2\r\n...'}` — the marker is
   present, proving the command actually ran end to end through the real
   wire protocol. **4/4 tools work**, the inverse of wq4397's 0/4 against the
   pre-rewrite protocol. wq4395's rewrite resolves the functional mismatch
   wq4397/wq4398 found in wq4396, even though wq4398's judgment ticket is
   still open — the resolution landed via wq4395's side, not a wq4396
   follow-up, which is worth noting on wq4398 so it isn't judged against
   stale evidence.
5. **Probed the one core code path the shipped suite never exercises**: the
   raw attach socket (`serve_attach`/`cmd_attach`, what `remote-cli attach` —
   Frank's actual human terminal — uses). The shipped test suite only starts
   `serve_control`; it never calls `serve_attach` at all. Wrote
   `/tmp/fworker-wq4399/probe_attach.py`: two concurrent raw clients on the
   attach socket, one types a command, both must see the mirrored output
   (the "shared" in shared-PTY). Result: both clients receive the marker —
   genuine two-way PTY sharing, not just single-client passthrough.
6. **Checked debian packaging** (`control`, `rules`, `install`,
   `remote-cli.service`, `changelog`, `source/format`) for internal
   consistency: unit name matches package name exactly (the wq#4071-class
   regression wq4395's own ISSUE-LOG row, 1472, already called out and
   avoided); `rules` stages `lib/remote_cli.py` to
   `/usr/lib/remote-cli/remote_cli.py` matching the `bin/remote-cli` shim's
   hardcoded path; no inconsistency found.

## Findings that don't rise to CONTRADICT, flagged for the record

- **Unauthenticated root-shell exposure.** `remote-cli.service` has no
  `User=`/`DynamicUser=` (unlike sibling `limpet-mcp-meta.service`, which
  uses `DynamicUser=yes` — the one other comparable Unix-socket daemon in
  `packaging/`), so it runs as root by default; combined with
  `RuntimeDirectoryMode=0777` and the control socket's explicit
  `os.chmod(path, 0o666)` (`remote_cli.py:173`, reasoned about in a comment —
  "Frank's human terminal and an agent process are different local users...
  the default umask would deny" — a deliberate tradeoff, not an oversight),
  **any local process on the box, as any user, can connect and run `exec`/
  `send` to get root code execution with zero authentication.** Grepped
  `packaging/` fleet-wide for `0o666`/`RuntimeDirectoryMode` — this is the
  only hit; no precedent elsewhere in this codebase for a world-writable
  control socket. Given the ticket's own fleet-wide install list includes an
  externally-deployed host ("Spectrio RE host (Purple4) when deployed"), this
  is worth a scoped follow-up (a dedicated group + `0660`, or `DynamicUser=`
  plus a shared group for the two intended local accounts) rather than
  leaving world-writable root exec as the default. Not a contradiction of
  anything the ticket or PR claimed — the ticket never promised access
  control — but a real gap for a tool shipping to more than one trust
  domain.
- **wq4396's hardcoded-5s-client-timeout bug (wq#4397 finding #6) is still
  present**, unchanged by this PR (it lives in `mcp/server.py`, which wq4395
  doesn't touch): `tool_exec(..., timeout=30.0)` forwards `timeout` only as a
  JSON field value; `RemoteCliClient`'s actual socket timeout is the
  hardcoded `DEFAULT_TIMEOUT = 5.0` (`mcp/server.py:49`), never set from the
  caller's value. My probe above used `timeout=5.0` so it didn't trip this;
  it's already tracked by wq#4397/wq#4398 and out of wq4395's scope, called
  out here only so wq#4398's judgment isn't working from stale information
  about which side of the pair still needs the fix.
- **Minor**: no `os.waitpid` on the forked PTY child
  (`SharedSession.__init__`/`close`) — if the child shell exits while the
  daemon keeps running, it leaves a zombie. Long-running
  single-shell-per-service-lifetime use case makes this low-impact, not a
  functional break.

## Verdict

**CONFIRM.** wq#4395's central claims hold under fresh, independent,
live-executed evidence: the shipped daemon runs, its shipped test suite
passes against a real PTY (6/6), the real `dpkg-buildpackage`+`dpkg-deb -c`
packaging check passes (2/2), its wire protocol genuinely matches wq4396's
MCP wrapper end-to-end (4/4 tools work against the real pair, re-running
wq4397's exact probe shape), and the one feature path the shipped tests never
touch (the raw attach socket / human-terminal sharing) works correctly when
probed directly. No contradiction found; no escalation needed. The two
secondary findings above (unauthenticated-root-exec permissions model;
wq4396's still-open hardcoded timeout) are real but don't falsify anything
wq#4395 or its PR claimed, and the second is already tracked under
wq#4397/wq#4398 rather than new here.

## Action taken

Posted CONFIRM to wq#4395 via `wq note`, with a pointer to this report and a
flag on wq#4398 that wq4395's rewrite already resolves the op/cmd protocol
mismatch its judgment pass is about to weigh in on. Not re-implementing
anything per ticket instruction ("Do NOT re-implement; verify").
