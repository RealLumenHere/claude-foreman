# wq#5102 verify-pass: wq#4921 (Commander off Horus onto Lumen)

Verdict: **CONFIRMED** for steps 1-3, the scope 4921 closed on. Steps 4-5 (Horus standby, failover
drill) are attended and tracked in wq#5101 (frank_gated). Verifier: orch-verify-batch-5099
(claude-opus-5-5), 2026-11-18 22:12 PST. Subject: PR #2643 (3cdacc2c).

## Evidence

| Check | Result |
|---|---|
| `verify_topology.py --min-conns 4` on Lumen | OK: dc-gateway active/enabled, cloudflared-mcp active/enabled, ha_connections 4, gateway answers MCP initialize + tools/list |
| `/etc/cloudflared-mcp/config.yml` ingress | `mcp-horus.lumennode.uk` and `mcp-backend.lumennode.uk` → `http://localhost:3100`; catch-all 404 |
| Old path on Horus (`ssh horus@192.168.8.209`; host now reports `seneca`) | `cloudflared.service` + `dc-gateway.service` masked and inactive |
| This verify session's own Commander calls | served from Lumen (Lumen paths, PIDs, `/home/lumen` tree) |
| `desktop-commander-mcp.service` on Lumen (legacy) | masked |

No fallback to the old path exists: the only live replica of the tunnel is Lumen's, and Horus
runs neither connector nor DC.

## Findings (not contradictions)

1. **Stale "horus" naming on the live path.** Twice this session a long call (>90 s) came back as
   `backend horus unreachable (timeout after 90000ms); no healthy backend available`. That text is
   mcpw `dist/worker.js:6888` and uses the backend `name` from the `MCP_BACKENDS` Worker secret,
   which still says `horus`. The tunnel is also still named `mcp-horus` with a `mcp-horus.lumennode.uk`
   hostname. Traffic goes to Lumen; only the labels are wrong, and they send an operator to the
   wrong box. Renaming the secret's backend label is a control-plane change (rule_no_unattended):
   attended only.
2. **A long Commander call is reported as an unreachable backend.** The 90 s worker timeout on a
   single backend surfaces as "no healthy backend", which reads like an outage. Long work should run
   detached (start_process + read_process_output), which this session did after the second hit.
3. **No live fallback Commander** (commander.rule_fallback is unmet). Already tracked: wq#5101, wq#4797.
