# wq#3892 — verify-pass of wq#3881 LIMPET: OpenOCD MCP server (JTAG/SWD control)

**Verdict: CONFIRMED.** The client's Tcl RPC command shapes, parsing, and lifecycle
handling check out against OpenOCD's own documentation and source, not just the PR's own
test suite. Added the bundle's missing `--verify` handler per `ops/VERIFY_CONTRACT.md`
(wq#1696) — `ops/wq3881-openocd-mcp` had no `install.sh` at all before this pass.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1862

## What wq#3881 claimed

- `openocd_client.py`: stdlib-only driver (subprocess + socket) over OpenOCD's Tcl RPC
  port, zero `mcp` import so it's unit-testable without the SDK.
- `server.py`: thin FastMCP wrapper, one `@mcp.tool()` per client method, no logic of its
  own.
- `tests/fake_openocd.py`: a fake `openocd` binary speaking the real Tcl RPC wire format
  (command + `0x1a` terminator) over a real loopback socket.
- `tests/test_openocd_client.py`: 17 tests, connect/disconnect lifecycle + every tool's
  happy path + invalid-input rejections (bad width, bad reset mode, not-yet-connected,
  empty write, missing flash image, non-positive scan length) per wq#2630.
- `server.py` itself is untestable in-sandbox: no `pip`/`pip3` on the worker Bash
  allowlist, so the `mcp` package can't be installed (kb/ISSUE-LOG.md row 1309).

## What I checked, independently of the diff's own narration

1. **Ran the shipped test suite.** `python3 -I -m pytest tests/test_openocd_client.py -q`
   → 17 passed, matching the README's claim exactly.
2. **Cross-checked every Tcl RPC command the client sends against OpenOCD's own
   documentation and source**, rather than trusting the fake double (which the same author
   wrote, so it can only confirm the author's own assumptions, not validate them against
   real hardware):
   - `read_memory address width count ['phys']` / `write_memory address width data
     ['phys']` — fetched `https://openocd.org/doc/html/General-Commands.html` via
     `searxng`; the client's `read_memory`/`write_memory` argument order and the
     optional trailing `phys` token match exactly (`openocd_client.py:172-187`).
   - `irscan [tap_name instruction]+ [-endstate state]` / `drscan tap [numbits value]+
     [-endstate state]` — same source; `boundary_scan`'s `irscan {tap} {ir_value:#x}` then
     `drscan {tap} {dr_length} 0` matches the tap/numbits/value ordering
     (`openocd_client.py:213-217`).
   - **The one claim worth real suspicion**: `disconnect()` sends a literal `"exit"`
     command over the RPC socket before killing the process (`openocd_client.py:106-118`).
     If real OpenOCD closed the connection immediately on `exit` *without* sending the
     `0x1a` terminator first, `_send_raw` would see `recv()` return `b""`, raise
     `OpenOCDError("...closed unexpectedly")`, and that exception is a `RuntimeError`
     subclass — NOT caught by `disconnect()`'s `except OSError: pass` — so it would
     propagate uncaught, skip `_kill_process()`, and leak the OpenOCD subprocess (and the
     JTAG/SWD probe it holds exclusively) on every disconnect. This is exactly the kind of
     bug a same-author fake can't catch, since the fake was built to match the same
     assumption. Pulled OpenOCD's real source to settle it:
     `src/server/server.c:833-848` (`handle_exit_command`) returns
     `ERROR_COMMAND_CLOSE_CONNECTION` for a Tcl/telnet-session `exit`, and
     `src/server/tcl_server.c:234-239` (`tcl_input`) shows the Tcl server handling that
     return value by sending `tcl_output(connection, "\x1a", 1)` — the terminator — before
     closing. So the fake's behavior (`fake_openocd.py:72-73`: reply `""` + terminator,
     then close) is correct, and `disconnect()`'s happy path is sound. No leak.
   - Also confirmed via `openocd.pdf`'s "Server Commands" section that `exit` only closes
     the current session (the daemon keeps running, still holding the probe) — which is
     exactly why `disconnect()` still needs its own explicit `_kill_process()` afterward
     to actually release the hardware. That part of the design is correct, not redundant.
3. **Read every method in `openocd_client.py` for resource leaks and error-path gaps**:
   `connect()`'s exception path always reaches `_kill_process()` before re-raising;
   `disconnect()` is idempotent when called without a prior `connect()` (returns cleanly,
   doesn't raise); `_await_rpc_socket` doesn't leave a dangling process on timeout. Found
   nothing that contradicts the PR's claims.
4. **Checked `server.py` against `openocd_client.py`** line by line: every `@mcp.tool()`
   is a one-line pass-through to the matching client method with matching argument names
   and defaults (`connect_target`→`connect`, `read_target_memory`→`read_memory`, etc.) —
   confirms the README's claim that server.py has no logic of its own to diverge from the
   tested client.
5. **Confirmed the sandbox gap is real, not an excuse**: `grep -n pip
   harness/worker-settings.json` from this verify worker (a different lane than the build
   worker) returns nothing either — the same constraint applies to me, so `server.py`
   genuinely can't be exercised end-to-end in any current worker sandbox. Matches
   kb/ISSUE-LOG.md row 1309, not a new finding.
6. **Bundle had no `--verify` handler** (`ls ops/wq3881-openocd-mcp/` — no `install.sh`),
   confirming the ticket's own callout. Added one modeled on
   `ops/wq3808-limpet-mcp-meta-server-a1/install.sh`'s precedent for a bundle with no
   fleet install target yet: `--verify` runs the primary tree's copy of
   `tests/test_openocd_client.py` under pytest and reports `MISS` if it fails, `VERIFY OK`
   otherwise, via the shared `ops/_verify.sh` helpers. Confirmed `server.py` is not
   referenced by `ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json` (it lists
   `ghidra`/`radare2`/`rea` as disabled placeholders, but not `openocd` — it isn't wired
   in at all yet), matching the "no deploy step exists" framing the new handler states.

## Verify handler limitation (logged plainly, not worked around)

This worker's Bash allowlist has no `bash ops/*/install.sh` (only `bash scripts/*`), so I
could not execute `ops/wq3881-openocd-mcp/install.sh --verify` directly in this sandbox to
observe its exit code. I instead:
- Ran the exact command the handler runs internally
  (`python3 -I -m pytest tests/test_openocd_client.py -q`) directly — 17 passed, which is
  what makes `--verify` print `VERIFY OK` and exit 0.
- Built the handler byte-for-byte on the same `_verify.sh` helper calls
  (`_vmiss`/`vdone`) as `wq3808-limpet-mcp-meta-server-a1/install.sh`, an already-landed
  bundle using the identical pattern for the identical "no deploy target yet" situation.

If the allowlist can be widened, the missing shape is `bash ops/*/install.sh --verify` (or
more narrowly `bash ops/wq3881-openocd-mcp/install.sh --verify`) so a verify worker can
execute a bundle's own handler instead of only its underlying test command.

## What would have broken this verdict (and didn't)

- If OpenOCD's real `exit` command terminated the whole daemon (not just the session),
  `disconnect()`'s explicit process-kill afterward would be reinstalling a probe link that
  was already gone, or racing a process that already exited — checked the real source,
  it isn't: `exit` only closes the current Tcl/telnet session (`server.c:833-848`).
- If OpenOCD's Tcl server closed the connection without echoing the `0x1a` terminator on
  `exit`, `disconnect()` would raise `OpenOCDError` uncaught and leak the subprocess —
  checked `tcl_server.c:234-239`, it sends the terminator first.
- If `read_memory`/`write_memory`/`irscan`/`drscan`'s argument order didn't match the
  real Tcl command signatures, every connected tool call would fail against real hardware
  despite all 17 tests passing (since the fake only validates against the author's own
  model of the protocol) — checked against `openocd.org`'s own command reference, they
  match exactly.
- If `server.py` had its own branching logic beyond pass-through, the untested-sandbox gap
  would be a real coverage hole rather than a documented non-issue — read every tool
  function, confirmed each is a single-line call-through.

No escalation to judgment needed — board this as verified.
