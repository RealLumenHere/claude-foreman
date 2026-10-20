# limpet-plugin-remote-cli (shared-terminal backend)

First-party shared-terminal plugin for Limpet. Wraps the tmux-backed
`shared-terminal` CLI (lumen-bin PR #262) as an MCP server so an agent and
Frank can drive and observe the same terminal session — no more
copy-paste/screenshot round trips for bench RE work.

History: wq#4395/wq#4396 built this on remote-cli, a custom shared-PTY daemon
with a Unix-socket JSON protocol. wq#4419 retired remote-cli — its attach
showed a blank screen until a key was pressed, it was pinned to 24x80 with no
resize/detach, and its VT100 subset could not render picocom/mtkclient/adb
output faithfully — and retargeted this plugin at shared-terminal, which gets
all of that from tmux (design: scratch/research/shared-terminal-solution.md).

## Tools

| Tool | Energize | Description |
|---|---|---|
| `snapshot` | No | Current visible screen (what an attached human sees); `--ansi` optional |
| `scrollback` | No | Last N lines of history (default 200), or all |
| `list` | No | Active shared sessions |
| `status` | No | Session metadata: size, clients, running command, cwd, log path |
| `wait` | No | Block until the screen matches a regex, or timeout |
| `send` | **Yes** | Type text or tmux key names into the shared session |
| `start` | **Yes** | Create a detached session, optionally running a command |
| `serial_console_open` | **Yes** | Open a picocom session on a UART device, sole owner of the port (wq#4433) |
| `serial_console_catch` | **Yes** | Spam a key until the screen matches a regex, or timeout — boot-window catch (wq#4433) |
| `serial_console_expect` | No | Wait for the screen to match a regex, no key spam (wq#4433) |

The read-only tools never write to the session (`wait`/`serial_console_expect`
only poll capture-pane). `send`, `start`, `serial_console_open`, and
`serial_console_catch` all mutate a terminal a human may be watching live, so
all four go through Sentinel's energize-confirm gate.

### serial-console (wq#4433)

`serial-console` (lumen-bin) is a thin layer over shared-terminal that gives
a UART device exactly one port owner: `open` starts `sudo picocom` inside a
shared-terminal session instead of a second, independent process fighting
for the port (the 2026-10-19 AP-303/C-130 failure this fixes: a pexpect
script spawned its own picocom on `/dev/ttyUSB0` while shared-terminal held
another, so keystrokes tangled and 2-3s boot windows were missed). `catch`
spams a key (default Enter, or named tmux keys) until the screen matches a
regex — the boot-window move for Hit-any-key/Press-f prompts. `expect` is the
same wait-for-regex as `wait`, through serial-console's own subcommand.
`snapshot`/`send`/`scrollback`/`status`/`list`/`kill` on a serial session are
already covered by the tools above — serial-console delegates them straight
to shared-terminal, so a full bootloader/failsafe RE flow (open the port,
catch a boot prompt, send a response, expect the next one) stays on this one
plugin.

There is no `exec` tool any more: the old remote-cli wrapper synthesized
run-and-wait-for-exit-code with printf marker lines because the daemon had no
such primitive. With tmux the composition is explicit and honest instead:
`send` the command, then `wait` for a prompt or output regex, then `snapshot`
or `scrollback` to read the result.

## How it talks to the backend

`mcp/server.py` is this plugin's own MCP stdio server. Each tool call shells
out to the shared-terminal CLI (default `/home/lumen/bin/shared-terminal`,
override with `SHARED_TERMINAL_BIN`), which owns all tmux interaction: socket
naming (`tmux -L shared`, override `SHARED_TERMINAL_SOCKET`), transcript
logging (`SHARED_TERMINAL_LOGS`), and capture semantics. The CLI surface is
unchanged by this plugin; a human at a shell and this server run the exact
same commands. `serial_console_open/catch/expect` shell out the same way to
the `serial-console` CLI (default `/home/lumen/bin/serial-console`, override
with `SERIAL_CONSOLE_BIN`), which itself drives shared-terminal underneath.

Text sent with `send` goes through the CLI's `--no-enter` form plus an
explicit `--key Enter`, so text beginning with `--` is never parsed as a
flag.

## Health

The shared tmux server's socket lives at `/tmp/tmux-$(id -u)/shared`;
`health.sh` (and the manifest's `[health] check`) test for it.

## Tests

```
python3 -m pytest tests/test_server.py -q
```

The live layer spawns a real tmux session through the real shared-terminal
CLI on an isolated tmux server socket (never the fleet's `shared` socket) and
skips if tmux or the CLI is absent.

## License

MIT (this wrapper). shared-terminal itself lives in lumen-bin.
