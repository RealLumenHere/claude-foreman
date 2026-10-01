# wq#3305 — dc-upgrade: atomic desktop-commander upgrade

## Incident

DC's npm upgrade to 0.2.46 left `~/.npm-global/bin/desktop-commander` missing
("not found") for a window during the `npm install -g` run, while the
`desktop-commander-mcp.service` unit's `Restart=always` kept respawning
against the gap: ~70 restarts, 2026-09-30 20:22-20:44 PDT. The unit stabilized
on its own once the install finished (`NRestarts` reset to 0 at 20:44).

`npm install -g` is not atomic against a running consumer: it unlinks the old
`bin/desktop-commander` symlink before the new package's `bin/` entries are in
place, so anything that execs that path during the install window gets ENOENT.

## Fix

`dc-upgrade.sh <version> <npm-global-prefix> <unit>`:

1. `npm install --prefix <staging-dir>` into a disposable
   `staging-dc-<version>/` directory — never the live `.npm-global` tree, so a
   bad install never touches what the unit currently points at.
2. Stop the unit (closes the respawn window entirely instead of racing it).
3. One `ln -sfn` to repoint `bin/desktop-commander` at the staged install's
   binary — a symlink rename is atomic, so there is no instant where the path
   resolves to nothing.
4. Start the unit, confirm `systemctl is-active`.

Rollback is the same shape: re-point the symlink at the previous
`staging-dc-<old-version>/.../desktop-commander` and restart.

## Also noted (frequency unchecked)

One client sent MCP protocol version `2026-07-28`, which supergateway
(bundled, pinned <= `2025-11-25`) rejected. Not actioned here — ticket calls
out only that the frequency is unchecked, not a required fix. Follow-up: add
a counter for rejected-protocol-version log lines in the existing watchdog
(`ops/wq2606-mcp-gateway-watchdog-a1/mcp-gateway-watchdog.sh`) if this recurs.
