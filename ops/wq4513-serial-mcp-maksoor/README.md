# wq#4513 - serial-mcp on Maksoor (UART access for agents)

**Status: INSTALLED and verified 2026-10-23 by serial-mcp-2df1e9. Live-device test NOT done (no adapter attached).**
Supersedes PR#2375 (the first kit, written by fworker-wq4513 which had no ssh path to Maksoor).

- Upstream: github.com/qarnet/serial-mcp, MIT, pinned **v0.9.3** = `e0f6df95cc073499447b84ec941cbb852660f637`, plus one local patch.
- Runs on Maksoor as `serial-mcp.service`, user `maksoor` (in `dialout`, so no root), binary `/opt/serial-mcp/bin/serial-mcp`.
- Endpoint **http://100.81.204.74:8400/mcp** (note `/mcp`). Bound to the Tailscale IP only; `ss -tlnp | grep 8400` must never show 0.0.0.0.
- Port allowlist: `/dev/ttyUSB*,/dev/ttyACM*`. Proven: `open /dev/ttyS0` returns "not in the allowlist" (list_ports still LISTS ttyS*; open is what is gated).
- Registered in Claude Code on Lumen at LOCAL scope for `/home/lumen/re` as `serial-maksoor` (`claude mcp list` = Connected).
- State: `/var/lib/serial-mcp/profiles.toml`; captures: `/var/log/serial-mcp/`.

## What the first kit / the ticket got wrong (all verified against v0.9.3 source and live)
1. **No config file exists.** Config is CLI flags only (`--transport --bind --allowlist --capture-dir --profiles-path`). The ticket's `/etc/serial-mcp/config.toml` is ignored.
2. **Endpoint path is `/mcp`.** There is no `/health` and `/` is not a route, so the kit's `curl http://IP:8000/` and `claude mcp add ... http://IP:8000` were wrong.
3. **Host-header 403 (the blocker).** upstream builds rmcp's `StreamableHttpServerConfig::default()`, whose DNS-rebinding guard allows only localhost Host headers. A server bound to the Tailscale IP refuses every real client ("Forbidden: Host header is not allowed"). `allowed-hosts.patch` (+21/-1 in `src/main.rs`) derives the allowed hosts from `--bind`. Re-check on every upstream bump; upstream may add a flag.
4. `ProtectHome=read-only` broke the profile store (default `~/.config`). Unit now uses `StateDirectory=serial-mcp` + `--profiles-path`.
5. Port is 8400 per the ticket (8000 is only upstream's default).
6. Protocol: server accepts `2025-11-25` (and the 2026-07-28 stateless mode). A hand-rolled `2025-06-18` initialize gets `-32601`. Use a real client or send `MCP-Protocol-Version: 2025-11-25`.

## RISKS (read before enabling more)
- **No authentication.** Anything on the tailnet that can reach 100.81.204.74:8400 gets write/DTR/RTS/BREAK on any attached allowlisted UART. The Tailscale ACL is the only gate. Tools are energizing-class (see shared-terminal notes): treat like `send`.
- **One owner per port.** serial-console/shared-terminal expect exactly one process on a port. Do not `open` a port via serial-mcp while a picocom/tmux session owns it (and vice versa).
- Young upstream (13 stars at pin time). Source was read for flags/transport only, not fully audited. Bumping the tag = re-do the patch + re-test.

## Run it
- Maksoor: `bash install.sh` (idempotent; refuses if the checkout is not the pinned SHA or the Tailscale IP changed).
- Lumen: `bash claude-mcp-add.sh`.
- Verify: `claude mcp list` from /home/lumen/re shows `serial-maksoor ... Connected`; 25 tools via tools/list.
- Rollback: `sudo systemctl disable --now serial-mcp`; previous binary kept as `/opt/serial-mcp/bin/serial-mcp.stock-v0.9.3` (unpatched) / `.prev`; `claude mcp remove serial-maksoor` (from /home/lumen/re); `~/.claude.json.bak.*` on Lumen.

## Add a device / test (needs physical hands)
Plug a USB-serial adapter into Maksoor. It appears as /dev/ttyUSB0 or ttyACM0 (maksoor udev rules + dialout already cover it). From /home/lumen/re run `claude`, ask it to `list_ports` then `open` the port with the right baud. Not yet done: wq filed for the live loopback test (jumper TX-RX on the adapter).
