# wq#2611 — nephthys-awareness target list += anubis:3100/health + bes:3100/health

Design: `MCP_CONTROL_PLANE_HA_DESIGN.md` §3.4, third bullet: "nephthys-awareness
already probes lumen dc-gateway; extend its target list with anubis/bes
:3100/health."

## Where this actually lives today

"nephthys-awareness" is not a literal running service any more. wq#589
(2026-08-23 Nephthys decommission, `notes` field, cited at
`kb/ISSUE-LOG.md` #754) relocated the awareness role to Bes:
`bes-awareness.service`. That unit and whatever config/target-list it reads
live on Bes itself — nothing under this repo or any `--add-dir` root mirrors
it, so there is no local file to diff against (unlike
`/home/lumen/sentinel/{config.yaml,probes/sentinel_probes.py}`, which are
outside-git-but-local-to-Lumen and were edited directly for this same
ticket's Gatus/aq half — see `README.md`).

## Why this BUILD lane can't apply it

```
$ /home/lumen/bin/fleet-exec grant
fleet-exec: DENY [grant:grant_missing_env]
```

`wq#2611`'s `needs` column (`sqlite3 agents/work-queue/queue.db "SELECT needs
FROM tasks WHERE id=2611"`) is `dir(/home/lumen/instructions/drafts)` only —
no `ssh(bes)` tag, so no exec grant was ever minted for this attempt. Same
structural gap `kb/ISSUE-LOG.md` #740 first logged for Seshat and #754
logged for Nephthys (wq#2609/wq#2613 hit the identical wall against
anubis/bes/nephthys). There is also no bare `ssh` on this lane's Bash
allowlist — only the `fleet-exec` broker, which is what's denied above.

## What to add, once a session has real Bes access

Add two targets to whatever structure `bes-awareness.service` reads its probe
roster from (find it with `ssh bes@<tailscale-ip> systemctl cat
bes-awareness.service` to get the ExecStart path, then read that script's
target list):

```
anubis:3100/health
bes:3100/health
```

These are the per-node DC gateway responsiveness endpoints defined by design
§3.3 (`mcp-gateway-watchdog.sh`'s own `localhost:3100/health` probe, same
port, exposed fleet-wide once Anubis/Bes run `dc-gateway.service` per §3.2 —
tracked separately in `ops/wq2607-dc-endpoint-anubis/` and the Bes
equivalent). awareness's existing Lumen dc-gateway probe (already live per
the design doc's own framing, "already probes lumen dc-gateway") is the
pattern to copy: same `:3100/health` shape, new hostnames.

```bash
# from a session with ssh bes@<tailscale ip> access (reconfirm the IP via
# lumen_state.py current bes tailscale_ip first, addresses rotate):
ssh bes@<ip> systemctl cat bes-awareness.service   # find ExecStart script path
# edit that script's target roster, add the two lines above
ssh bes@<ip> systemctl restart bes-awareness.service
```

## Status

BLOCKED on remote access, logged at `kb/ISSUE-LOG.md` #1064. Not worked
around — no destination file exists locally to stage a real diff against,
so this document is the apply instructions rather than a `.diff`.
