# wq#2607 — DC endpoint on Anubis: dc-gateway + own cloudflared tunnel + Access app

Design: `MCP_CONTROL_PLANE_HA_DESIGN.md` §3.2 (Anubis: i5 4C/8T, 32G, 3w uptime, load
0.5; verified 2026-09-25 no node, no cloudflared). Target: `anubis` tailscale
`100.121.17.5` (confirmed live via `fleet-exec run lumen -- tailscale status`,
2026-09-25 — matches the design doc's own §3.5 citation of that address).

## Status: artifacts staged, NOT deployed

Every file this ticket asked for that is pure content (units, scripts, config,
inventory entry) is written and reviewed in this directory. Nothing has been
installed, copied, started, or requested against Anubis, Cloudflare DNS, or Cloudflare
Access. Root cause: this BUILD worker's fleet-exec grant is `ssh(lumen, run)` only —

```
$ /home/lumen/bin/fleet-exec grant
{"attempt": 1, "expires": "2026-09-25T09:51:59Z",
 "grants": [{"kind": "ssh", "node": "lumen", "verbs": ["run"]}], "ticket": 2607}
$ /home/lumen/bin/fleet-exec run anubis -- true
fleet-exec: DENY [grant:no_ssh_run]: no ssh grant for anubis with verb run
```

and even a widened grant would not close the gap: `fleet-exec`'s static policy
(`harness/fleet-exec-policy.yaml`) only exposes `run` for a fixed read-only probe set
(`cat`, `ls`, `systemctl status/is-active/is-enabled/show/cat/list-*`, `journalctl`,
`ss`, `tailscale status`, `docker ps/logs`, ...) — no `apt-get`, `npm`, `tee`, or
`systemctl restart` outside two allowlisted Lumen units. Those live under `sudo_cmds`,
which the script's own docstring says is "v2 (fleet-exec has no `sudo` verb yet)".
`copy` is similarly narrow: destination must be one of `copy_dirs`, a `<node>/home/<user>/lumen-drop`
staging dir per node, and Anubis isn't even in that list yet. Logged as
`kb/ISSUE-LOG.md #740` per doctrine (append the issue, then solve what's solvable).

This is a structural gap in the v1 broker (`wq#1332`), not a decision that needs
Frank — full autonomy still requires an actual execution path, which does not exist
for a BUILD-lane worker today. Filed here rather than worked around.

## What's in this directory

| file | maps to |
|---|---|
| `dc-gateway.service` | the DC gateway unit (byte-adapted from Lumen's live `desktop-commander-mcp.service`, paths only — content fetched via `fleet-exec run lumen -- systemctl cat desktop-commander-mcp`, not guessed) |
| `dc-gateway-hardening.conf` | §3.3 T1 hardening drop-in: `MemorySwapMax=1G CPUWeight=1000 IOWeight=1000 Nice=-5 OOMScoreAdjust=-900` |
| `dc-gateway-logging.conf` | secrets-at-rest parity with Lumen's `no-body-logs-journald.conf` |
| `dc-enforce-config.py` | Anubis copy of `/home/lumen/bin/dc-enforce-config.py` (only `CFG` path changed; `BLOCKED`/`FORCE` byte-identical, fetched not guessed) |
| `mcp-gateway-watchdog.sh` + `.service` + `.timer` | §3.3 T2: 1-min responsiveness probe, 2-consecutive-fail restart, 300s cooldown, >2/30min flap guard that suspends auto-restart and alerts |
| `cloudflared-ingress-anubis.yml` | the ingress stanza to add to Anubis's `cloudflared` `config.yml` |
| `wq2607_fleet_tools.diff` | the `context/fleet_tools.yaml` entry (that file is outside version control — `context/` is `.gitignore`d in the `minerva` repo, same shape as wq#1720/wq#2095 — so this is the citable record of a live edit, not a diff that gets applied by `git apply`) |
| `install.sh` | read-only `--verify` of the `fleet_tools.yaml` edit once it's applied by hand; installs nothing (contract in `../_verify.sh`) |

## Exact remaining commands (run with real shell access to Anubis, e.g. `ssh anubis@100.121.17.5`, or by a future worker whose grant covers `anubis` with an install-capable verb)

```bash
# 1. Node LTS + pinned packages (Lumen's own pins, verified 2026-09-25)
ssh anubis@100.121.17.5 -- 'curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs'
ssh anubis@100.121.17.5 -- 'npm i -g supergateway@3.4.3 @wonderwhy-er/desktop-commander@0.2.46'

# 2. dc-enforce-config.py + unit + drop-ins (this directory's copies)
scp ops/wq2607-dc-endpoint-anubis/dc-enforce-config.py anubis@100.121.17.5:/home/anubis/bin/dc-enforce-config.py
ssh anubis@100.121.17.5 -- 'sudo install -m0644 -D /dev/stdin /etc/systemd/system/dc-gateway.service' < ops/wq2607-dc-endpoint-anubis/dc-gateway.service
ssh anubis@100.121.17.5 -- 'sudo install -m0644 -D /dev/stdin /etc/systemd/system/dc-gateway.service.d/dc-gateway-hardening.conf' < ops/wq2607-dc-endpoint-anubis/dc-gateway-hardening.conf
ssh anubis@100.121.17.5 -- 'sudo install -m0644 -D /dev/stdin /etc/systemd/system/dc-gateway.service.d/dc-gateway-logging.conf' < ops/wq2607-dc-endpoint-anubis/dc-gateway-logging.conf
ssh anubis@100.121.17.5 -- 'mkdir -p /home/anubis/logs/supergateway && touch /home/anubis/logs/supergateway/dc-gateway.log && chmod 0600 /home/anubis/logs/supergateway/dc-gateway.log'
ssh anubis@100.121.17.5 -- 'sudo systemctl daemon-reload && sudo systemctl enable --now dc-gateway'

# 3. T2 watchdog (needs passwordless sudo for the two restart targets only)
ssh anubis@100.121.17.5 -- 'sudo install -m0755 -D /dev/stdin /home/anubis/bin/mcp-gateway-watchdog.sh' < ops/wq2607-dc-endpoint-anubis/mcp-gateway-watchdog.sh
ssh anubis@100.121.17.5 -- 'sudo install -m0644 -D /dev/stdin /etc/systemd/system/mcp-gateway-watchdog.service' < ops/wq2607-dc-endpoint-anubis/mcp-gateway-watchdog.service
ssh anubis@100.121.17.5 -- 'sudo install -m0644 -D /dev/stdin /etc/systemd/system/mcp-gateway-watchdog.timer' < ops/wq2607-dc-endpoint-anubis/mcp-gateway-watchdog.timer
ssh anubis@100.121.17.5 -- "echo 'anubis ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dc-gateway, /usr/bin/systemctl restart cloudflared' | sudo tee /etc/sudoers.d/mcp-gateway-watchdog && sudo chmod 0440 /etc/sudoers.d/mcp-gateway-watchdog"
ssh anubis@100.121.17.5 -- 'sudo systemctl daemon-reload && sudo systemctl enable --now mcp-gateway-watchdog.timer'

# 4. cloudflared — install + tunnel MUST be created ON Anubis (the 06-29 gotcha:
#    `tunnel create` run on Lumen mints a tunnel owned by Lumen's cert.pem/UUID, which
#    breaks the "one UUID per node, individually addressable" design in §3).
ssh anubis@100.121.17.5 -- 'curl -fsSL https://pkg.cloudflare.com/cloudflared-stable-linux-amd64.deb -o /tmp/cloudflared.deb && sudo dpkg -i /tmp/cloudflared.deb'
ssh anubis@100.121.17.5 -- 'cloudflared tunnel login'          # interactive browser auth — Frank
ssh anubis@100.121.17.5 -- 'cloudflared tunnel create anubis-mcp'
# capture the printed UUID and credentials-file path, then merge
# ops/wq2607-dc-endpoint-anubis/cloudflared-ingress-anubis.yml's `ingress:` block into
# Anubis's config.yml (tunnel: <uuid>, credentials-file: <path>), enable --metrics
# localhost:20241 (the watchdog's cloudflared probe target), and:
ssh anubis@100.121.17.5 -- 'sudo systemctl enable --now cloudflared'

# 5. DNS CNAME + CF Access app — needs the wq#2614 CF API token
#    (Workers Scripts:Edit + Workers KV:Edit + Zone:Workers Routes:Edit +
#    Access:Apps:Edit + DNS:Edit; token file was shredded 06-29, per
#    MCP_CONTROL_PLANE_HA_DESIGN.md §6 — PHYSICAL TASK for Frank, not autonomous).
#    Use the ANUBIS tunnel's OWN uuid (from step 4), not Lumen's — same 06-29 gotcha:
CF_TOKEN=$(cat /home/lumen/.secrets/cf_token)   # once minted
ANUBIS_TUNNEL_UUID=<from step 4>
curl -sX POST "https://api.cloudflare.com/client/v4/zones/<zone_id>/dns_records" \
  -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
  --data "{\"type\":\"CNAME\",\"name\":\"mcp-anubis\",\"content\":\"${ANUBIS_TUNNEL_UUID}.cfargotunnel.com\",\"proxied\":true}"
# CF Access self-hosted app "mcp-anubis.lumennode.uk", policy = reuse the existing
# 'Worker service token' e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0 (pattern of mcp-failover
# 06-29 — no new Worker secret). Exact API calls: mirror whatever created the
# mcp-failover Access app; that app's config was not readable from this worker's
# grant either (Access apps aren't a `run_readonly_cmds` target) — read it back with
# `cloudflared access` or the dashboard before scripting the anubis app so the policy
# JSON is copied, not re-typed from memory.
# Prove: `curl -i https://mcp-anubis.lumennode.uk/mcp` -> 403 (no token);
#        `curl -i -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." \
#           -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
#           https://mcp-anubis.lumennode.uk/mcp` -> 200 with a tools/list result.

# 6. SSH keys on Anubis for lumen@100.117.66.102 + every other fleet node
#    (so a session landing on Anubis during a Lumen outage can still run the standard
#    SSH-wrapped fleet commands, §3.2/§3.5). Generate once, then test BatchMode to
#    EVERY node lumen_state.py currently lists as tailscale_ip.
ssh anubis@100.121.17.5 -- 'test -f /home/anubis/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N "" -f /home/anubis/.ssh/id_ed25519 -C anubis-fleet'
# distribute the pubkey to lumen + every fleet node's authorized_keys (each node's own
# admin path — no single broker copies into another node's authorized_keys), then:
ssh anubis@100.121.17.5 -- 'ssh -i /home/anubis/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=10 lumen@100.117.66.102 -- true && echo OK'
# repeat -o BatchMode=yes per fleet node (isis, bes, sobek, maat, hathor, ... — current
# roster: `python3 /home/lumen/minerva/lumen_state.py current "" tailscale_ip`)

# 7. lumen_state ingest + regen (minerva/context is deliberately outside every worker
#    grant, wq#1467 — credentials mirrored into context/sections — so this needs a
#    session with real minerva access, not this BUILD lane):
python3 /home/lumen/minerva/lumen_state.py ingest anubis mcp_endpoint "https://mcp-anubis.lumennode.uk/mcp" --source wq2607
python3 /home/lumen/minerva/lumen_state.py regen
```

## fleet_tools.yaml

Applied by hand (that file is outside version control, same as wq#1720/wq#2095):
apply `wq2607_fleet_tools.diff`'s new `mcp_ha_endpoints` group to
`/home/lumen/minerva/context/fleet_tools.yaml`, bump its `updated:` date, re-render
with `fleet-tools-render`, then `install.sh --verify` from this directory should print
`VERIFY OK`. The entry as staged says "prepared not deployed" — update that wording
once steps 1-7 above actually land, don't leave it stale.

## What this ticket actually closes today

- The complete, reviewed artifact set for the Anubis DC endpoint (unit, T1 hardening,
  logging, enforce-config, T2 watchdog + timer, cloudflared ingress stanza),
  content-matched against Lumen's real live unit rather than guessed.
- The `fleet_tools.yaml` inventory entry (as a diff, per the established
  outside-version-control pattern) recording the endpoint's true current state.
- `kb/ISSUE-LOG.md #740`: the fleet-exec v1 broker cannot execute any of this
  ticket's install/DNS/Access/ssh-key steps for a BUILD-lane worker — no grant path
  exists today even before the wq#2614 CF token question. Both gaps (broker verb, CF
  token) block on something outside this worker's reach; both are stated exactly, not
  worked around.
