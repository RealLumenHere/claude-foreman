# wq#4921: Commander carried by Lumen, Horus standby

Horus froze 5+ times in one session while it carried the only cloudflared connector
from claude.ai to the fleet. Commander now runs on Lumen. This directory holds the
read-only verifier for that topology and the attended runbook for the Horus standby.

| File | What |
|---|---|
| `verify_topology.py` | Read-only check, run on Lumen. Both units active and enabled, cloudflared edge connections >= `--min-conns`, gateway answers MCP `initialize` + `tools/list` (reuses `../wq3294-commander-ha/mcp_probe.py`). Exit 0/1, `--json` for machine use. |
| `test_verify_topology.py` | 8 tests: healthy, masked unit, active-but-disabled unit, too few edge connections, metric absent, metrics unreachable, dead gateway, gateway error body. `python3 -m pytest -q test_verify_topology.py`. |

## Ticket steps: state on 2026-11-18

| # | Step | State | Evidence |
|---|---|---|---|
| 1 | cloudflared on Lumen as a connector of the existing tunnel | DONE | `cloudflared-mcp.service` active+enabled since 2026-11-12 23:46Z, `/usr/bin/cloudflared` 2026.7.3, `--config /etc/cloudflared-mcp/config.yml --metrics localhost:20242`; `cloudflared_tunnel_ha_connections 4` |
| 2 | dc-gateway on Lumen | DONE | `/etc/systemd/system/dc-gateway.service`: mcp-proxy on `0.0.0.0:3100`, active+enabled; `mcp_probe.py http://127.0.0.1:3100/mcp` OK |
| 3 | mcpw routes to Lumen | DONE | `MCP_BACKENDS` → `mcp-backend.lumennode.uk` → this tunnel, and Lumen holds its only live replica (lumen_state `commander.topology`). Public hostname answers 403 without the Access service token, as expected. |
| 4 | Horus as standby connector | NOT LIVE | lumen_state `horus.role`: `cloudflared.service` and `dc-gateway` masked on Horus since 2026-11-13. Activation below is attended. |
| 5 | Failover test | NOT RUN | `commander.rule_no_unattended` forbids failure tests on the live control plane without Frank driving. Procedure below. |

The facts for steps 3 and 4 come from lumen_state, written by the previous attempt.
This worker could not re-read them: ssh to Horus is not on the worker allowlist and
`fleet-exec run horus` returned `DENY [grant:grant_missing_env]`. Run step A0 first.

## Why the standby has to relay to Lumen and not serve its own DC

From Cloudflare's tunnel availability docs (fetched 2026-11-18): "Replicas do not support
traffic steering… a request… is forwarded to the geographically closest replica. If that
connection fails, Cloudflare retries with other replicas, but there is no guarantee about
which one is chosen." Lumen and Horus sit on the same LAN, so if both replicas are live
Cloudflare can send any request to either one. If Horus ran its own dc-gateway, Commander
calls would land on Horus or Lumen at random, each with a different filesystem and
process table.

So the warm standby on Horus is a relay. Its ingress for the tunnel hostname points at
Lumen's gateway, which keeps every call on one DC. That covers a dead cloudflared
process on Lumen, lost edge connections from Lumen, and a Lumen cloudflared upgrade. It
does not cover Lumen itself going down. For that, the fallback is the cold Horus-local DC
in section C, also attended.

## A. Activate the Horus relay replica (attended, Frank driving over SSH, not Commander)

A0. Read-only: confirm the current state before changing anything.

    ssh horus@100.83.19.124 'systemctl is-enabled cloudflared.service dc-gateway.service; ls -l /etc/cloudflared/ 2>&1'
    ssh horus@100.83.19.124 'curl -s -o /dev/null -w "%{http_code}\n" -m 5 http://192.168.8.160:3100/mcp'

The second command should print `406`, meaning the gateway is reachable and refused a
bare GET. If it prints `000`, Lumen's :3100 is unreachable from Horus (lumen_state
records this as the state on 2026-11-18). Fix that first. Check Lumen's firewall with
`ssh lumen@100.117.66.102 'sudo ufw status numbered'` and allow 192.168.8.209 to 3100/tcp
only, not the whole LAN.

A1. Back up the Horus tunnel config (rule 6), then edit its ingress so that the
`mcp-backend.lumennode.uk` hostname points at `http://192.168.8.160:3100`, with the same
hostname and path rules as Lumen's `/etc/cloudflared-mcp/config.yml`. The ingress has to
match Lumen's, because each replica of a locally-managed tunnel uses its own config.

A2. Unmask and start, detached (instinct 6):

    ssh horus@100.83.19.124 'sudo systemctl unmask cloudflared.service && sudo systemd-run --unit=wq4921-horus-cf-start --collect systemctl enable --now cloudflared.service'

Leave `dc-gateway` on Horus masked. The relay replica does not use it.

A3. Verify:

    ssh horus@100.83.19.124 'systemctl is-active cloudflared.service; journalctl -u cloudflared.service -n 20 --no-pager | grep -c "Registered tunnel connection"'
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4921-commander-lumen/verify_topology.py --min-conns 4'

## B. Failover test (attended)

Frank stays on SSH over Tailscale the whole time, because Commander is the thing being
cut. Make one Commander call from claude.ai after each step.

1. Baseline: both replicas up (A3 passes). Make a Commander call and confirm it works.
2. Stop Lumen's connector: `ssh lumen@100.117.66.102 'sudo systemctl stop cloudflared-mcp.service'`. Make a Commander call. It should succeed through the Horus relay to Lumen's DC. Time it.
3. Restore: `ssh lumen@100.117.66.102 'sudo systemctl start cloudflared-mcp.service && sleep 10 && python3 /home/lumen/foreman/ops/wq4921-commander-lumen/verify_topology.py --min-conns 4'`.
4. Stop the Horus connector: `ssh horus@100.83.19.124 'sudo systemctl stop cloudflared.service'`. Make a Commander call. It should succeed through Lumen.
5. Restore: `ssh horus@100.83.19.124 'sudo systemctl start cloudflared.service'`.
6. Record the result. Frank fills in the observed outcome for `<result>`: `ssh lumen@100.117.66.102 'python3 /home/lumen/minerva/lumen_state.py ingest commander fallback "<result>" --valid-from $(date -u +%F) --source wq#4921 && /home/lumen/bin/state-regen'`.

Rule 7: every service stopped in B must be running again before the session ends.

## C. Lumen down: cold Horus-local Commander (attended)

Use this only while Lumen's cloudflared-mcp is down and Lumen's gateway is unreachable,
because both replicas must never serve different DCs at once. On Horus: unmask and start
`dc-gateway.service`, point the Horus ingress at `http://localhost:3100`, and restart
`cloudflared.service`. To revert, set the ingress back to `http://192.168.8.160:3100`,
restart cloudflared, and stop and mask `dc-gateway` before Lumen's connector comes back.
