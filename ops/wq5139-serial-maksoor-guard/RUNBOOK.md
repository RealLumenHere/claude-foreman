# serial-maksoor behind lumen_mcp_guard + energize-confirm (wq#5139)

Found by wq#5097: the local-scope `serial-maksoor` registration (project `/data/lumen/archive/re`) was a raw
streamable-HTTP entry to `http://100.81.204.74:8400/mcp` (qarnet serial-mcp 0.9.3 on Maksoor, wq#4513).
All 25 tools, no allowlist, no output masking, and in interactive sessions no energize gate:
`action_gate.py` (which classifies `mcp__serial-*`) runs only inside foreman workers.

Chain now: Claude Code -> `~/.local/share/mcp/serial-maksoor/launch.sh` -> `lumen_mcp_guard`
(allowlist, pins, mask-secrets, 512 KiB cap, untrusted-output wrapping, audit
`~/.local/state/lumen-mcp-guard/serial-maksoor.jsonl`) -> `http_stdio_bridge.py` (energize gate) -> serial-mcp.

The bridge exists because the guard only drives stdio backends.

## Deploy

```
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5139-serial-maksoor-guard/install.sh'
```

Backs up `~/.claude.json` to `/home/lumen/backups/claude.json.pre-wq5139.*`, installs bridge +
`energize_confirm.py` copy + pins, re-registers `serial-maksoor` (stdio, local scope), runs `--verify`.
Sessions already open in `/data/lumen/archive/re` keep the raw HTTP server until restarted.
`--verify` needs Maksoor's serial-mcp up (it lists the live backend).

Rollback:
`ssh lumen@100.117.66.102 'cd /data/lumen/archive/re && claude mcp remove -s local serial-maksoor && claude mcp add -s local -t http serial-maksoor http://100.81.204.74:8400/mcp'`

## Policy (`serial-allow.txt`, 20 of 25)

| Class | Tools | Gate |
|---|---|---|
| read-only | list_ports, list_profiles, list_connections, get_status, get_log, read, compute_checksum | none |
| session | open, open_profile, close, reconnect | none (see caveat) |
| energizing | write, transact, capture_boot, send_break, set_dtr_rts, set_flow_control, reconfigure, flush, clear_log | bridge needs `confirm_token` |
| hidden | configure, save_profile, delete_profile, rollback_profile, export_log | guard: Unknown tool |

Hidden: the four profile tools rewrite the profiles TOML on Maksoor; `export_log` writes a host file.
New upstream tools stay hidden until reviewed.

Energizing calls need an operator token, capability `serial.<tool>` (same names as action_gate):

```
ssh -t lumen@100.117.66.102 'python3 /home/lumen/foreman/harness/energize_confirm.py issue serial.transact'
```

`-t` matters: issue refuses without a TTY. Single-use, 5 min. Hand the token to the session; the
bridge verifies it and strips it before serial-mcp sees it.

Caveats:
* `open` is ungated. On most USB-UART adapters opening the port asserts DTR, which resets an
  Arduino-style target wired to DTR. Same as before this bundle; gate it too if that matters.
* `open` auto-creates a persistent profile on Maksoor (`auto-ft232r-usb-uart-N`): server-side
  behaviour, not reachable through the hidden tools.
* In a foreman worker the action_gate hook consumes the token first, so the bridge then refuses
  (fail closed). No worker loads serial-maksoor today (local scope, archive/re only).

## Pins

`serial-pins.json` = sha256 of the 20 guarded definitions (as the agent sees them, `confirm_token`
included) from `install.sh --gen-pins`, 2026-11-19. After a serial-mcp upgrade: read the new
definitions, regenerate, commit.

## Evidence (2026-11-19)

* `test_serial_guard.py`: 10 pass (offline: token required / wrong capability / expired /
  single-use / stripped before backend; read needs none).
* `probe_guarded.py ~/.local/share/mcp/serial-maksoor/launch.sh`: 12/12 vs live serial-mcp, 20 tools listed.
* Live session on FT232R `/dev/ttyUSB0` (loopback jumper) through the guard: open -> read -> get_status
  -> close OK; transact without token refused before reaching the UART.
