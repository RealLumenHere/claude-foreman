# limpet-plugin-remote-cli

First-party shared-terminal plugin for Limpet. Wraps wq#4395's remote-cli
(a pure-Python shared-PTY tool, Unix socket IPC, no tmux/screen dependency)
as an MCP server so an agent and Frank can drive and observe the same
terminal session — no more copy-paste/screenshot round trips for bench RE
work.

## Tools

| Tool | Energize | Description |
|---|---|---|
| `snapshot` | No | Read the current screen state |
| `logs` | No | Read scrollback |
| `send` | **Yes** | Type raw keystrokes into the shared session |
| `exec` | **Yes** | Run a command in the shared session and wait for its exit code |

`snapshot`/`logs` are pure reads of what's already on screen. `send`/`exec`
both mutate a PTY a human may be watching live, so both go through
Sentinel's energize-confirm gate — see `mcp/server.py`'s module docstring
for why `exec` is gated even though the wq#4396 ticket text only named
`send` explicitly.

## Wire protocol

`mcp/server.py` is this plugin's own MCP stdio server (remote-cli is not
itself an MCP server). Each tool call opens a fresh connection to
`/run/remote-cli/shared.sock` (override with `REMOTE_CLI_SOCKET_PATH`),
writes one JSON line, reads one JSON line back, and disconnects.

The top-level request key is `cmd`, and remote-cli implements exactly five
commands (`remote-cli:271-292`):

| Request | Reply |
|---|---|
| `{"cmd": "snapshot"}` | `ok`, `screen`, `rows`, `cols` |
| `{"cmd": "scrollback", "lines": 200}` | `ok`, `text` |
| `{"cmd": "send_keys", "text": "...", "enter": false}` | `ok` |
| `{"cmd": "resize", "rows": 40, "cols": 120}` | `ok` |
| `{"cmd": "status"}` | `ok`, `status` |

The MCP tool names above are this plugin's API; they are not the wire command
names. `snapshot` → `snapshot`, `logs` → `scrollback`, `send` → `send_keys`.

`tests/fixtures/remote_cli_protocol.json` is the committed copy of that
contract, and `tests/test_server.py` asserts the wrapper never puts anything
else on the wire. wq#4398 added both after wq#4397 found the first cut sending
`{"op": ...}` with invented command names — every tool got back
`{"ok": false, "error": "unknown cmd None"}` from the real daemon while the
original suite stayed 8/8 green, because its fake dispatched on the same
invented key.

### `exec` is synthesized, not a remote-cli command

remote-cli has no synchronous run-and-wait-for-exit-code primitive: it owns a
PTY a human may be attached to, so the only way to run something *in that
session* is to type it. `exec` therefore types one line — a start marker, the
command handed to `eval`, and a unique completion marker carrying `$?` — then
polls `scrollback` until the completion marker appears and returns what lies
between the two markers. Consequences worth knowing before you use it:

- the shared session must be a POSIX shell at a prompt. If a human left it in
  `vim`/`less`/a REPL, `exec` types into *that* and returns a timeout.
- `timeout` is the real wall-clock budget for the whole run, honored across
  every poll (the first cut ignored it and capped every call at 5s).
- `output` is carved out of raw scrollback, so it keeps the PTY's `\r\n` and
  any escape sequences the command emitted.
- the command runs under `eval`, so it is parsed as a script fragment, not
  typed line by line: embedded newlines run as successive lines and all of
  their output comes back, but history expansion (`!!`) does not happen and a
  syntax error is reported against `eval`'s line.
- `warning` appears in the result when the start marker had already aged out
  of the daemon's scrollback cap — i.e. `output` may be missing the command's
  first lines, rather than that being silent.

Why two markers rather than keying off the shell's echo of the typed line
(wq#4405): a shared PTY does not guarantee that echo exists (`stty -echo`, a
password prompt) or that there is one of it. Keying off it returned ok:true
with a correct `exit_code` and the wrong `output` — the last line only for a
multi-line command, a stale prompt string for a session with echo off.

This lives client-side on purpose: remote-cli is already deployed fleet-wide
(Lumen, Maksoor) and keeps its minimal five-command protocol.

## Tests

```
python3 -m pytest tests/test_server.py -q
```

The live layer spawns the real `remote-cli` binary (`REMOTE_CLI_BIN`, default
`/home/lumen/bin/remote-cli`) over a real PTY and skips if it is absent.

## License

MIT (this wrapper). remote-cli's own license is whatever wq#4395 installs.
