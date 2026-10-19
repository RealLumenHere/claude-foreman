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
| `exec` | **Yes** | Run a command in the shared session |

`snapshot`/`logs` are pure reads of what's already on screen. `send`/`exec`
both mutate a PTY a human may be watching live, so both go through
Sentinel's energize-confirm gate — see `mcp/server.py`'s module docstring
for why `exec` is gated even though the wq#4396 ticket text only named
`send` explicitly.

## Wire protocol

`mcp/server.py` is this plugin's own MCP stdio server (remote-cli is not
itself an MCP server). Each tool call opens a fresh connection to
`/run/remote-cli/shared.sock` (override with `REMOTE_CLI_SOCKET_PATH`),
writes one JSON line `{"op": ..., ...}`, reads one JSON line back, and
disconnects.

## License

MIT (this wrapper). remote-cli's own license is whatever wq#4395 installs.
