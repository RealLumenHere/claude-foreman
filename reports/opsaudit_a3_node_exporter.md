# wq#753 — A3 BUILD: node_exporter fleet-wide into the existing VictoriaMetrics

Campaign: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md`, finding F6 ("zero node
CPU/RAM/VRAM/disk series for any of the 15 nodes"). This closes F6 with measured data, not
another estimate.

## Result

**14/14 reachable Linux hosts now run node_exporter, verified live in the existing
VictoriaMetrics on Bes.** VM's metric-name count went from 37 (all HA/plug/PG&E/OTEL —
zero node series) to **458**, including `node_cpu_seconds_total`, `node_memory_*`,
`node_filesystem_*`, `node_load1`, and a custom `amdgpu_vram_{used,total}_bytes` textfile
series on the 4 hosts with an AMD GPU. A Grafana dashboard proves it end to end.

```
$ ./opsaudit_a3_deploy_node_exporter.sh --verify-only
[VERIFIED] anubis (100.121.17.5:9100) -> 200
[VERIFIED] apep (100.82.2.72:9100) -> 200
[VERIFIED] bes (100.110.156.91:9100) -> 200
[VERIFIED] gameboy (100.118.56.59:9100) -> 200
[VERIFIED] isis (100.100.129.24:9100) -> 200
[VERIFIED] lumen (100.117.66.102:9100) -> 200
[VERIFIED] maat (100.105.219.34:9100) -> 200
[VERIFIED] nephthys (100.98.74.87:9100) -> 200
[VERIFIED] seshat (100.121.243.124:9100) -> 200
[VERIFIED] sia (100.111.15.120:9100) -> 200
[VERIFIED] sobek (100.86.156.91:9100) -> 200
[VERIFIED] thor (100.112.151.100:9100) -> 200
[VERIFIED] thoth (100.75.1.108:9100) -> 200
[VERIFIED] toilet (100.70.94.19:9100) -> 200
[VM] series with up==1 for job=node_exporter_fleet: 14 (via lumen-powerdash-vmtunnel 127.0.0.1:18428)
```

Grafana: `http://100.110.156.91:3000/d/wq753-node-exporter/fleet-node-exporter-wq23-753`
(tailnet-only, same Grafana instance as the existing power dashboard — anon-viewer, or
admin/lumen2026! per `bes.grafana` in lumen_state). 5 panels: targets-up stat, CPU busy %,
memory used %, AMD GPU VRAM used %, root-fs used % — all by `node` label, all confirmed
returning live series through Grafana's own datasource proxy (not just the raw VM API).

## Why this is root-free (read this before touching it)

The automation account on every fleet node can SSH in but has **no passwordless sudo**,
and this session's guard policy hard-blocks the `sudo` verb outright (not "ask", blocked).
So nothing here touches a root-owned file or a system (`/etc/systemd/system`) unit:

- node_exporter binary lives at `~/.local/node-exporter/bin/node_exporter` per host.
- Autostart is a **per-user systemd unit** (`~/.config/systemd/user/node_exporter.service`,
  managed with `systemctl --user`). Reboot persistence comes from `loginctl enable-linger`
  run **self-targeted** (no username argument) — that specific form is unprivileged; a user
  may linger themselves without root, verified live on `bes` before relying on it fleet-wide.
- Same pattern for the AMD-GPU hosts: a `vram-textfile.timer` (`systemd --user`, 30s) runs
  `vram_textfile.sh`, which reads `/sys/class/drm/card*/device/mem_info_vram_{used,total}`
  (world-readable, no privilege needed) and writes an atomic `.prom` file for node_exporter's
  `--collector.textfile.directory`.
- **VictoriaMetrics ingestion is a second, independent, unprivileged vmagent** running as the
  `bes` user (`~/.local/vmagent-nodeexp/`, its own `scrape.yml`, its own
  `systemctl --user` unit), scraping all 14 node_exporter targets and remote-writing to the
  *same* VictoriaMetrics instance the existing HA-power vmagent already writes to
  (`http://127.0.0.1:8428/api/v1/write`). **The existing root-owned
  `/etc/vmagent/scrape.yml` (wq#342/344, HA power sensors) was never opened, let alone
  edited.** Zero risk to the running power dashboard, zero backup needed there, no
  change-safety gate to clear on that file. This is the one thing in the ticket's
  CONSTRAINTS section ("backup any edited scrape config") that turned out not to apply —
  by construction, not by oversight.

Everything is idempotent: rerun `opsaudit_a3_deploy_node_exporter.sh` any time; each step
checks current state first (binary copy is copy-then-rename so it never fights a running
process for the same inode; `enable` + `restart` rather than `enable --now` so a version
bump actually takes effect on rerun).

## Roster and what got excluded

Roster source: `lumen_state.py current <host>.tailscale_ip`, cross-checked against a live
SSH-reachability pass (`fleet-telemetry`, wq#744) run fresh at deploy time
(2026-08-22T05:29Z). 22 tailscale-addressable names exist; 14 are real, reachable, running
Linux hosts. The other 8:

| host | tailscale_ip | why excluded |
|---|---|---|
| apis | 100.89.147.95 | DEAD hardware, confirmed 2026-08-13 (no power, won't POST). e-waste, not a target. |
| hathor | 100.80.71.13 | PARKED — plug cut 2026-08-11, tailnet-offline since ~07-17. Powered off by design. |
| wadjet | 100.116.217.15 | RETIRED from service 2026-08-02 (Frank), powered off. `ssh: Connection timed out` confirms. |
| khepri | 100.120.16.78 | Timed out at measurement time. lumen_state's last fact says "POWERED ON 2026-08-14" — this may be a state drift (box off since, or a network blip) rather than a permanent exclusion; worth a quick re-check next time someone is on that box. |
| 3dprinter | 100.114.82.85 | Timed out. Klipper print server, low value as a monitoring target even when up. |
| be3600 | 100.81.177.14 | GL-iNet Slate 7 **router** (OpenWrt), not a systemd/Linux general-purpose host — node_exporter's user-unit approach doesn't map onto it even when reachable. Timed out anyway at measurement time. |
| pbox | 100.68.189.7 | Timed out. Embedded plug-controller box, not a general compute target. |
| hapi / hapi-1..N | *(none yet)* | BC-250 blade cluster bring-up is still delayed on cooling work (`hapi.bringup_eta`) — no tailscale_ip fact exists yet, nothing to reach. Add to the roster the day a blade boots. |

`p13`/`p14` (Riley's PC / Parker's PC) are explicitly recorded as "NOT a Lumen fleet node" in
lumen_state and were never in scope.

## wq#744 coordination (per ticket CONSTRAINTS)

wq#744's SSH-fanout collector (`/home/lumen/bin/fleet-telemetry`) already shipped and still
works — it has no timer wired to it (manual/on-demand only), so it costs nothing to leave in
place. Per the ticket instruction, it stays as the **fallback for the 8 excluded hosts** above
(and for a fast one-shot fleet snapshot when VM/Grafana aren't handy) — it doesn't need an
agent runtime on the target, just SSH + `/proc`, `/sys` reads. It is fully superseded by this
ticket for the 14 hosts now in VictoriaMetrics: those get real history, PromQL, alerting
potential, and a Grafana panel, none of which the JSON snapshot ever had.

## PromQL for MC's `/api/fleet`

Datasource: the existing VictoriaMetrics on Bes, reachable from Lumen (where
mission-control's backend already runs, `127.0.0.1:8136`) via the existing
`lumen-powerdash-vmtunnel.service` SSH tunnel at `http://127.0.0.1:18428` — no new
tunnel needed, VM's query API is still 127.0.0.1-only on Bes by design (wq#341, Bes is a
vault standby, telemetry is deliberately capped).

```promql
# fleet reachability (1 = node_exporter answering)
up{job="node_exporter_fleet"}

# CPU busy % by node
100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",job="node_exporter_fleet"}[5m])) * 100)

# memory used % by node
100 * (1 - (node_memory_MemAvailable_bytes{job="node_exporter_fleet"} / node_memory_MemTotal_bytes{job="node_exporter_fleet"}))

# root filesystem used % by node
100 * (1 - (node_filesystem_avail_bytes{job="node_exporter_fleet",mountpoint="/"} / node_filesystem_size_bytes{job="node_exporter_fleet",mountpoint="/"}))

# AMD GPU VRAM used % by node+card (isis, lumen, nephthys, toilet only — the 4 hosts with amdgpu sysfs)
100 * amdgpu_vram_used_bytes{job="node_exporter_fleet"} / amdgpu_vram_total_bytes{job="node_exporter_fleet"}

# 1-minute load average by node (contention signal for R5 — "all builds run on Lumen")
node_load1{job="node_exporter_fleet"}
```

Example call MC's backend can make today:
```
curl -sg 'http://127.0.0.1:18428/api/v1/query?query=100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",job="node_exporter_fleet"}[5m])) * 100)'
```

## Artifacts

- `opsaudit_a3_deploy_node_exporter.sh` — the reusable, idempotent deploy tool (roster +
  install + verify, `--verify-only` for a read-only check). Re-run it after any fleet
  roster change instead of hand-deploying a 15th node.
- `opsaudit_a3_grafana_dashboard.json` — the dashboard body as POSTed to
  `/api/dashboards/db`; re-apply with:
  `curl -u admin:lumen2026! -H 'Content-Type: application/json' -X POST --data-binary @opsaudit_a3_grafana_dashboard.json http://100.110.156.91:3000/api/dashboards/db`

## Gaps / follow-ups (not blocking, noted for A5/A13)

- `khepri` should get a one-off re-check — it may just be a momentary blip, not a real
  exclusion, and it's the only excluded host whose last known state was "on."
- R5 ("all builds run on Lumen") is now measurable per the audit's own framing — `node_load1`
  and CPU-busy-% by node are live. That's A5's input, not this ticket's job to interpret.
- MC's `/api/fleet` still needs the actual backend wiring to call the PromQL above through
  the existing tunnel; this ticket proves the data and the query, not the MC route.
