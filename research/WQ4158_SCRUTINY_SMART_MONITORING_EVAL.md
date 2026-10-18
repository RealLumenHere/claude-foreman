# wq#4158: Scrutiny (analogj/scrutiny) for fleet-wide SMART monitoring — evaluation

## Ticket's own motivating example doesn't hold

The ticket cites "Nephthys already showing AER errors" as the case for urgency. That AER
is **PCIe Advanced Error Reporting** (`AER: Multiple Correctable error message received` on
root port `00:01.0`), not a drive SMART symptom — it's a PCIe link/power issue on the
MacPro6,1 chassis, already tracked by a dedicated leading-indicator alarm:
`ops/wq2851-aer-storm-alarm/` (polls `journalctl -k 'PCIe Bus Error'`, correlated to three
prior abrupt power losses on nephthys/isis/hathor/khepri — `ops/wq2851-aer-storm-alarm/README.md:14-27`)
and re-confirmed live at `reports/wq3925_nephthys_aer_watch.md:1-45`. Scrutiny ingests
`smartctl`/NVMe SMART-log attributes; it has no PCIe AER counter and would not have
surfaced, explained, or predicted this event. The fleet already has the right tool pointed
at this specific symptom — it just isn't a SMART tool. This doesn't kill the underlying ask
(fleet-wide drive-health visibility is still a real gap), but the ticket's own evidence for
urgency is a category error worth correcting before anyone cites it again.

## Drive inventory: "20+ SSDs/NVMes" — verified, actually an undercount

`research/fleet_hardware.json` (generated 2026-10-12 from `lumen_state.py current`, the
fleet's own hardware-of-record) lists per-node `storage` strings. Counting distinct
SMART-addressable physical drives (ATA/SATA/NVMe; excluding eMMC, which has no classic ATA
SMART attribute table):

| Node | Drives | Type |
|---|---|---|
| lumen | 9 | 1x NVMe boot + 3x PM951 (RAID5) + 4x ~512GB (RAID0) + 1x SATA HDD backup |
| isis, hathor, khepri, nephthys | 1 each (4) | Apple AHCI SSD blades (SM0256/SM0256G) |
| thor, maat, sobek, bes, sia, anubis, aten, horus, maksoor, gameboy, farida, opnsense-fw | 1 each (12) | NVMe |
| seshat | 1 | mSATA (1TB NVMe physically removed 2026-09-09, wq#1341) |
| apep | 1 | M.2 SATA (+58GB eMMC, not counted) |
| ptah, sekhmet | 1 each (2) | NVMe `/data` (eMMC root not counted) |

That's **29 SMART-capable physical drives already on the inventory**, before counting
`thoth`'s unenumerated media drives or `apis`'s unspecified 114GB root. The ticket's "20+"
is conservative, not inflated — confirmed, not guessed, against `research/fleet_hardware.json`.

Two caveats the number doesn't carry on its own:
- `khnum` and `neith` are eMMC-only (`~29GB eMMC`, no NVMe/SATA) — `smartctl` has no
  equivalent attribute table for eMMC CSD registers, so these two nodes have **nothing**
  for Scrutiny to show even if a collector were installed.
- The four Apple AHCI blade SSDs (isis/hathor/khepri/nephthys) are a non-standard form
  factor. They're SATA/AHCI-attached, so plain `smartctl -a /dev/sdX` likely works the same
  as any SATA SSD, but this is **not verified from this sandbox** — `smartctl` is absent
  from `harness/fleet-exec-policy.yaml`'s `run_readonly_cmds` allowlist, so no headless
  worker (this one included) can run it against live hardware. Flagging as unverified
  rather than asserting it works, per the "never guess, verify" doctrine.

## Architecture (from upstream docs, searx query "analogj scrutiny SMART monitoring
collector architecture requirements", 2026-10-18)

- **Hub**: `analogj/scrutiny` (or the `-omnibus` tag, which bundles InfluxDB) — one web
  app + time-series store, deployed once. Matches the existing `fleet-config/docker/monitoring/`
  pattern (currently just `gatus`, port 8418) — a sibling `scrutiny` service block on the
  same compose file/ansible role (`fleet-config/ansible/roles/monitoring/tasks/main.yml`)
  is the natural fit, not a new stack.
- **Collector**: a small Go binary (no Docker required on the collected node — confirmed via
  the project's own "SMART Monitoring with Scrutiny across machines" multi-host tutorial and
  its Windows/Synology binary install docs), run via cron/systemd timer, needs root (or
  equivalent) to execute `smartctl`/NVMe passthrough, and POSTs results to the hub's
  `/api/collect` over plain HTTP. Low resource per the ticket's framing — this matches
  `gatus`'s existing footprint on lumen, not a heavy addition.

## Who can actually get a collector today — this is the real gap

`fleet-config/ansible/inventory/hosts.yml` only lets playbooks target 6 live nodes:
`lumen` (gateway), `isis` (inference), `sobek` (db), `anubis` (git), `maat`+`bes` (workers).
Everything else sits in the `readonly` group, whose own header comment is explicit:
**"No playbook may target this group. `site.yml` never includes it."** That's
`hathor, ptah, sekhmet, horus, neith, khnum` (+ `thor`, tier-0 hands-off). `khepri` and
`nephthys` — two of the four nodes the ticket's own motivating example names — aren't in
the inventory file **at all**.

`harness/fleet-exec-policy.yaml` (the headless-worker broker policy) doesn't close this
gap either: its `ssh:` allowlist (`fleet-exec-policy.yaml:192`) is read-only
(`run_readonly_cmds` only — `true/uptime/hostname/uname/cat/ls/stat/df/du/free/systemctl
status/journalctl/ss/ip addr/tailscale status/docker ps/docker logs`, none of which is
`smartctl`), and there is no `sudo`/install verb yet ("sudo is v2" — `fleet-exec-policy.yaml:42,232`).
So a headless build worker (this ticket's own execution context) cannot install a collector
on any node outside the 6-node Ansible group, full stop — not a permission to request, a
verb that doesn't exist yet.

This isn't a dead end for the fleet as a whole, though: `ops/wq2844-netdata-macs/install.sh`
already proves the actual pattern for the Mac Pros and other `readonly`-group nodes —
netdata got installed on `hathor`/`khepri`/`nephthys` via a purpose-built idempotent
install script, run once by an interactive session with real ssh keys (outside the
fleet-exec broker's restricted verb set entirely), verified live by
`reports/wq3079_verify_wq2844_netdata_reachable_hathor_khepri.md`. A Scrutiny collector on
those same nodes would follow the identical shape: a reviewed `install.sh` + systemd timer,
landed by PR, run once by whoever has that ssh access — not something this ticket's
execution context (headless build worker) can do itself, and not a Frank-gate either (it's
an execution-environment boundary, not a decision pending Frank's input).

## Cheaper-alternative check, worth doing before standing up a second stack

Every active node already gets `netdata` (apt package, `fleet-config/ansible/roles/monitoring/tasks/main.yml:4-8`
for the 6 Ansible nodes; `ops/wq2844-netdata-macs/install.sh` for the Mac Pros — v2.11.x
confirmed live, `reports/wq3079_verify_wq2844_netdata_reachable_hathor_khepri.md:13`).
Netdata's `go.d.plugin` has shipped a `smartctl` collector since well before 2.11 — it
auto-detects drives and charts SMART attributes through the same dashboard the fleet
already watches for CPU/fan/thermal (`ops/fan_floor_alarm.py`). Neither
`ops/wq2844-netdata-macs/netdata.conf.template` nor `ops/wq394-netdata-canary/netdata.conf.template`
explicitly enables or disables it, and no report in this repo mentions checking whether it's
active or whether `smartmontools` is installed fleet-wide (a prerequisite either way, for
Scrutiny's collector or netdata's). That check — SSH into one already-netdata'd node (e.g.
`hathor`, which has the fleet-exec `ssh` read-only grant) and look for `smartctl` charts on
its existing `:19999` dashboard — is a five-minute way to find out whether the fleet already
has 80% of this ticket's value for zero new containers, before building a second
hub+collector stack that duplicates it. Flagging as the first thing a follow-up ticket
should do, not asserting an answer either way — unverified from this sandbox, same
`smartctl`-off-the-allowlist constraint as above.

## Verdict: conditional GO, phased

Adopt Scrutiny as the dedicated SMART dashboard **if** the netdata check above comes back
negative (no existing SMART charts) or inadequate (netdata's SMART view is thinner than
Scrutiny's failure-prediction/history view and the fleet wants the latter specifically).
Rollout is naturally two phases given the infrastructure split above:

1. **Phase 1 (Ansible-reachable, no new access needed)**: hub container on `lumen`
   alongside `gatus` in `fleet-config/docker/monitoring/`, collectors on `lumen, isis,
   sobek, anubis, maat, bes` via the existing `monitoring` ansible role. Covers 6 of the
   29 inventoried drives directly, plus `lumen`'s own 9.
2. **Phase 2 (needs an interactive ssh session, not this execution context)**: collector
   via a `wq2844`-style `install.sh` on the `readonly`-group nodes and the two
   not-in-inventory Mac Pros (`khepri`, `nephthys`) that the ticket actually names. Skip
   `khnum`/`neith` (eMMC-only, nothing for `smartctl` to read).

Not a NO-GO: the tool itself is a good fit (low resource, matches the existing
hub/collector shape the fleet already runs for `gatus`/`netdata`). The gate is
infrastructure access, not tool suitability — and that gate is exactly why this ticket is
scoped as an evaluation, not a deployment: landing Phase 1 needs a `fleet-config` PR adding
the compose service + ansible task (ordinary review, no policy change), and Phase 2 needs
someone with real fleet ssh to run an install script once, the same as every prior
Mac-Pro-targeted tool in this repo.
