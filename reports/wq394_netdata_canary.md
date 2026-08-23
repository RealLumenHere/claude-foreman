# wq#394 — netdata agent canary: edge ML anomaly detection + built-in MCP

Canary node: **lumen** (T5810, hostname `lumen`, tailnet IP `100.117.66.102`).
Deployed 2026-08-22, agent-only, no SSH used (executed locally on `lumen` via
`lumen-run --root`, since this host is the worker's own machine). All config
and automation live under `ops/wq394-netdata-canary/` in this repo.

## What's live

- netdata v2.11.0 installed via native package (kickstart script,
  `--non-interactive --stable-channel --disable-telemetry`). **Unclaimed** —
  `netdata-claim.sh` was never run, agent is not connected to Netdata Cloud.
  (`/api/v3/info` reports `"reason": "Agent is not claimed yet"`.)
- Web/API/MCP bound to the tailnet IP only: `bind to = 100.117.66.102:19999`.
  Verified `ss -tlnp` shows the listener on `100.117.66.102:19999` only —
  `curl localhost:19999` gets connection-refused. `allow * from` restricted to
  `localhost 100.*` as defense-in-depth behind the bind.
- ML anomaly detection: `[ml] enabled = yes` (also the v2.x default). Verified
  live — the MCP `initialize` response text and the `find_anomalous_metrics`
  tool both confirm k-means anomaly scoring is running per metric.
- MCP live at `http://100.117.66.102:19999/mcp`. Read-only by netdata's own
  design (doc: "Dynamic configuration is not exposed"). Full JSON-RPC smoke
  test performed with curl: `initialize`, `tools/list` (13 tools:
  list_metrics, query_metrics, list_nodes, find_anomalous_metrics,
  find_correlated_metrics, list_running_alerts, execute_function, etc.),
  and `tools/call` against `list_nodes` / `query_metrics` all returned real
  data over the tailnet IP.
- Config backed up to `/etc/netdata/netdata.conf.orig-wq394` before edit.
- Side effect during install: the netdata apt repo package pulled in a libc
  security update as a dependency; `needrestart` auto-restarted 16 unrelated
  services on this host (fail2ban, redis-server, smbd, lumen-dashboard,
  lumen-command-receiver, lumen-memoryd, thoth-origin, etc.). Verified all 16
  are `active` post-restart — no service left down.

## Finding: journal + procs are NOT available agent-only (contradicts ticket scope)

The ticket asks for MCP access to "metrics+anomaly scoring+journal+procs, no
SSH", under a hard "Agent-only (no cloud) ... Do NOT touch netdata cloud"
constraint. Verified empirically that these conflict in netdata v2.11.0:

- `list_functions` reports an `access` tag per function. `processes`,
  `systemd-journal`, `network-connections`, `netdata-streaming`, and several
  others are tagged `["signed-in","same-space","sensitive-data"]`.
- Calling `execute_function` for `processes` **with a valid MCP API key**
  (`/var/lib/netdata/mcp_dev_preview_api_key`) returns:
  `"You are not authorized to execute function 'processes' on node 'lumen'."`
- Functions tagged `anonymous-data` (metrics, alerts, mount-points,
  network-interfaces, containers-vms, systemd-services, sensors,
  block-devices) work fully unclaimed, no key needed — reachability on the
  tailnet is the only gate.

So: **metrics + anomaly scoring + alerts work fully agent-only** (the core of
the ticket's autonomy use case). **journal + procs + live network-connections
require claiming the agent to Netdata Cloud** — this is a real product
constraint in the current (dev-preview) MCP implementation, not a config gap.
I did not claim the agent, per the ticket's explicit no-cloud instruction.
This is a decision point for whoever reviews the fleet-rollout call: accept
metrics/anomaly/alerts-only MCP as sufficient for P1 dispatcher/correctors,
or revisit the no-cloud constraint. Flagging rather than guessing.

## Footgun found: `bearer token protection = yes` breaks MCP auth

Tried enabling netdata's `bearer token protection` for extra defense-in-depth.
With it `= yes`, **every** MCP call — including `initialize`, and including
calls with a correct `Authorization: Bearer <mcp_dev_preview_api_key>` header
— returns `"MCP API key is required when bearer token protection is enabled"`.
This setting appears to expect a different (Cloud-issued) bearer token, not
the local dev-preview MCP key. Left `= no`; documented in
`ops/wq394-netdata-canary/netdata.conf.template` so nobody re-trips this.
Network isolation (tailnet-only bind) plus the MCP dev-preview key remain the
two real access-control layers.

## Optional remote-write to Bes VM — deferred

Bes's VictoriaMetrics (tailnet IP `100.110.156.91`) refuses connections on
:8428 from `lumen`'s tailnet position (`curl -v` → connection refused). This
matches wq#753's approach (vmagent runs locally on Bes and scrapes/pushes,
VM itself isn't exposed on the tailnet). Wiring netdata's exporting engine to
Bes would need either a pull-based scrape target added to Bes's vmagent
config, or an ingest port opened on Bes — both are changes to shared infra on
a different host than this worker could reach (no SSH capability in this
session; this class of change is what wq#753 needed elevated access for).
Left unwired since the ticket marks this "optional". Follow-up ticket below.

## 48h power/CPU measurement gate — started, not decided

Started, **not completed** — a 48h wall-clock wait cannot happen inside one
synchronous worker session. What's running now:

- `ops/wq394-netdata-canary/sampler.py`, deployed to
  `/usr/local/lib/wq394-netdata-canary/`, run every 5 minutes by
  `wq394-netdata-sampler.timer` (systemd, root, enabled).
- Each tick appends a row to `/var/lib/wq394-netdata-canary/measurements.csv`:
  RAPL package-0 energy (`/sys/class/powercap/intel-rapl:0/energy_uj`) →
  watts, total system CPU%, and netdata's own CPU%/RSS via
  `systemctl show netdata -p CPUUsageNSec` / `MemoryCurrent` (cgroup
  accounting — isolates netdata's cost from host noise directly, no
  before/after whole-system diffing needed for the CPU side).
- Soak start marker: `/var/lib/wq394-netdata-canary/soak_start` (unix ts,
  written at deploy time, 2026-08-22 ~06:41 UTC).
- Two manual ticks were fired to validate the math (see
  `ops/wq394-netdata-canary/analyze.py` docstring for the go/no-go formula:
  `estimated_netdata_watts = avg_package_watts * avg_netdata_cpu_share`).
  Both ticks were during the post-install ML training burst
  (`netdata_cpu_pct` ~25% over a 14s window) — **not representative**, which
  is exactly why this needs the full 48h window rather than a snapshot.
  > **Errata (wq#865, 2026-08-23):** the sentence above was checked against
  > 24h of soak data and is wrong on both counts. ~25% is steady state, not a
  > burst (min 21.8%, max 26.2%, flat across 24h), and it is not ML — per-PID
  > cgroup attribution puts the ML threads at 0.8% of one core, ~3% of
  > netdata's CPU, with `apps.plugin` and the cgroups collector accounting for
  > most of it. The reading is also per-core, not a share of the host: ÷44
  > cores it is 0.562%, which passes the gate. Left in place rather than
  > edited out so the original call stays visible next to the check.
  > See `reports/wq865_canary_cpu_attribution.md`.
- `analyze.py` refuses to produce a recommendation before 48h have elapsed
  since the marker, and prints GO/HOLD against placeholder thresholds
  (<2% avg CPU share, <3W estimated draw) that whoever runs it should sanity
  check against the actual numbers rather than trust blindly.

## MCP smoke test

Full JSON-RPC protocol test via curl against `http://100.117.66.102:19999/mcp`
with the dev-preview API key: `initialize` (200, ML-anomaly-detection text
confirmed), `tools/list` (13 tools), `tools/call` on `list_nodes` and
`query_metrics` (both returned real data), unauthenticated `tools/call` on
`anonymous-data` tools also verified working (confirms tailnet-bind is the
real gate for that class), `execute_function` on a `sensitive-data` function
verified correctly rejected unclaimed. A full `claude -p --mcp-config` nested
session (research doc's suggested (b) validation) was attempted but there is
no bare `claude` binary on PATH in this worker's environment to shell out to
— the protocol-level test exercises the identical request/auth path a real
`claude -p` MCP client would use, so I'm treating it as sufficient rather than
spending more turns chasing the nested-session path.

## Follow-up

Filed wq#797 — due 48h after soak_start (~2026-08-24 06:41 UTC):
read `measurements.csv` via `analyze.py`, write the GO/HOLD recommendation,
and hand the actual fleet-rollout decision to Frank (this ticket only
canaries one node; rolling to the fleet is a separate call). That ticket also
carries the deferred Bes remote-write wiring and the journal/procs
cloud-claim tradeoff as open questions for that decision.
