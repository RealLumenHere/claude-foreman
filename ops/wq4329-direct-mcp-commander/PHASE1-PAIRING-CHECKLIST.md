# wq#4329 Phase 1 — physical tasks (queued, not gates)

Per the full-autonomy instinct: everything decidable has been decided (approach,
unit shape, rollout scope) above in README.md. These two items are queued because
they are a physical act nobody but Frank can perform — not because a decision is
pending.

1. **[FRANK] Pair Lumen.** On Lumen: `npx @wonderwhy-er/desktop-commander@latest
   remote`, then complete the browser OAuth device-flow check (sign in, match the
   pairing code, click Verify Device). One-time per machine.
2. **[FRANK] Pair Maksoor.** Same step, run on Maksoor (100.81.204.74).
3. **[FRANK] Add the connector.** In claude.ai / Claude Desktop / Claude Code
   settings, add Remote Desktop Commander as a remote MCP connector
   (`https://mcp.desktopcommander.app/mcp`) and confirm both paired devices are
   selectable.

After all three: `install.sh <node-user> --verify` on each node confirms Node
>= 18 and the staged unit matches, then an authorized deploy path (not a BUILD
sandbox — see install.sh's header) installs `remote-desktop-commander.service`
so the pairing survives a terminal close / reboot.
