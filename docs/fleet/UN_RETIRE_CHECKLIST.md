# docs/fleet/UN_RETIRE_CHECKLIST.md — bringing a decommissioned node back into service

Companion to `docs/fleet/retired_services.yaml` (which records why a service was
retired) — this file is the inverse: what to verify/do when a *node* comes off a
retirement/decommission and is expected to resume its normal role. Un-retiring a
node is un-masking/re-enabling every unit that was stopped at decommission time,
not just restoring power — a node can be physically back and still be functionally
retired if any of those units were never re-armed.

## Checklist

1. **Power/network**: node responds on tailscale, base OS services up.
2. **Re-enable every unit stopped at decommission** — `systemctl list-units
   --state=inactive,disabled` on the node against whatever list was disabled at
   retirement time (check `docs/fleet/retired_services.yaml` and the node's own
   history for what was stopped, not just what's obviously missing).
3. **`enable node's dc-gateway/cloudflared`** if the node is (or was) an MCP
   control-plane endpoint. Root cause this item exists (wq#2613's "T5"): Nephthys's
   `nephthys-mcp-failover.service` and its cloudflared tunnel were `STOPPED
   2026-08-23 03:27Z` and `DISABLED` as part of decommissioning the node. The node
   was un-retired on 2026-09-18, but nobody re-ran the un-retire side of that
   decision — the two units stayed disabled. The 09-24 control-plane outage then
   found Nephthys, the one configured failover target, dead by config: independent
   of that day's actual power losses, it had been non-functional for five weeks
   (`MCP_CONTROL_PLANE_HA_DESIGN.md` §1, `... Node un-retired 09-18, units never
   re-enabled. Failover target was dead by config ...`). A node un-retire is
   incomplete until `systemctl enable --now <gateway-unit> cloudflared` has
   actually been run and verified active on that node — "the node is back" and
   "the node's services are back" are different facts, and only the parity check
   in `ops/wq2613-dc-endpoint-parity-check-a1/` currently catches a re-drift here
   for the four DC endpoint nodes specifically.

   **The precise mechanism (wq#2609)**: Nephthys's 2026-08-23 decommission
   (wq#589) bundled three independent roles onto one ticket and relocated two
   elsewhere — `bes-awareness.service` and `bes-mcp-failover.service` (its own
   CF tunnel `1eaa5126`), per wq#589's own `notes` field — while leaving
   Nephthys's own mcp-failover/cloudflared disabled by design, not paused. The
   2026-09-18 "un-retire" only exercised the *unrelated* LLM-serving role
   (bringing Nephthys back as an inference rung via the `fleet-node add` path,
   `harness/node_lifecycle.py`'s five LLM-serving targets: gateway config, key
   allowlist, dispatcher config, models.yaml, `LOCAL_POOL_MODELS`). That path
   has no step that reads a node's own prior decommission ticket, so any role
   outside its scope — here, mcp-failover/cloudflared — stayed dark with
   nothing alerting (the guards this repo has watch for units that *should* be
   running, not "was this correctly left off, or just forgotten?").
4. **Confirm the parity check sees it**: if the node is one of the four DC
   endpoints (lumen, anubis, bes, nephthys), run
   `dc-endpoint-parity-check.py` (or wait for its nightly timer) and check for a
   fresh open `aq` row under source `dc-parity` for that node — a clean run
   with no drift row is the actual close-out signal, not "systemctl start"
   returning 0.
5. **Update the roster**: `docs/fleet/UN_RETIRE_CHECKLIST.md` step 3 done ⇒
   update that node's `status` in the `mcp_endpoints` roster
   (`lumen_state.py ingest mcp_pool mcp_endpoints ...`, see
   `ops/wq2613-dc-endpoint-parity-check-a1/lumen_state_ingest.sh`) so
   `fleet_tools.yaml`'s `commander_mcp` entry stops reading as stale.
6. **For any node, not just the four DC endpoints**: before closing an
   un-retire ticket, find that node's own decommission ticket (`sqlite3
   agents/work-queue/queue.db "SELECT id, title, notes FROM tasks WHERE
   detail LIKE '%<node>%' AND (title LIKE '%decommission%' OR title LIKE
   '%retire%')"`) and read its `notes` field, not just its `detail` — notes
   record what was *actually done*, which is what a revival ticket must
   reconcile against. Enumerate every role/unit that ticket touched and make
   an explicit call per role not already covered by the current ticket's own
   scope: "relocated elsewhere, stays retired here" or "should also come back
   here" — write the call down so the next audit doesn't re-discover it as a
   mystery (this doc's own §3 is exactly that call, made after the fact,
   for Nephthys). Nephthys's mcp-failover specifically: Bes is the durable
   primary target (wq#589) and stays so; re-enabling it on Nephthys too
   (wq#2609) is an explicit optional/redundant extra ("pref 4 ... not
   counted in N=3"), not an undo of that relocation.
