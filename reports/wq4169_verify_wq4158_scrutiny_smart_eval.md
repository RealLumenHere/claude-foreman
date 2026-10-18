# wq#4169 — verify wq#4158 EVALUATE: Scrutiny SMART monitoring across fleet drives

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: #2110 (merged to `main` as 336ae85e). Deliverable is docs-only — new
`research/WQ4158_SCRUTINY_SMART_MONITORING_EVAL.md` (+135) and a `TOOL_REGISTRY.md` entry
(+23), no code/config touched.

## Claim under test

Doc recommends **CONDITIONAL GO, phased**: adopt Scrutiny as the dedicated SMART dashboard
if a cheap netdata-collector check comes back negative/inadequate. Phase 1 (hub + collectors
on the 6 Ansible-reachable nodes, no new access needed); Phase 2 (readonly-group nodes +
khepri/nephthys, needs a wq#2844-style install script run by an interactive ssh session, not
this ticket's headless-build-worker context). Also corrects the ticket's own motivating
example: nephthys's cited "AER errors" is PCIe AER, not a SMART symptom.

## What I checked

- **AER category-error correction** — `ops/wq2851-aer-storm-alarm/README.md` exists and is
  exactly what's described: a PCIe-AER leading-indicator alarm (root port `00:01.0`,
  correlated to abrupt power losses on nephthys/isis/hathor/khepri).
  `reports/wq3925_nephthys_aer_watch.md` exists. Scrutiny ingests `smartctl`/NVMe SMART logs,
  not PCIe AER counters — the correction is right.
- **Drive inventory (29 drives)** — recomputed independently from
  `research/fleet_hardware.json` (33 nodes total) rather than trusting the doc's table:
  summed every node's `storage` field myself. Doc's table and arithmetic (9+4+12+1+1+2=29)
  match exactly, including which nodes are grouped together (Apple AHCI blades: isis,
  hathor, khepri, nephthys; the 12-node NVMe group: thor, maat, sobek, bes, sia, anubis,
  aten, horus, maksoor, gameboy, farida, opnsense-fw). `khnum`/`neith` eMMC-only exclusion
  and apep's eMMC-not-counted note both check out against the raw JSON.
  - **Gap found, not a contradiction**: `wadjet` (`fleet_hardware.json`) is `status:
    online-unprovisioned` with `storage: "256GB Apple SSD blade (assumed mac-pool standard;
    not individually verified)"` — a live node with a real (if unverified) drive. The doc
    doesn't count it in the 29 and doesn't flag it alongside `thoth`/`apis` as a further
    known undercount. Doesn't change the verdict: wadjet is in neither
    `hosts.yml`'s six Ansible groups nor its `ssh:` allowlist, so it would land in Phase 2
    exactly like khepri/nephthys either way. The doc's other excluded nodes (`hapi`, `m80s`,
    `mac-pool-spares`, `sentinel`, `limpet`, `m70s-2`, `3dprinter`) are legitimately excluded
    — unprovisioned/offline/no-disks-yet per their own `status` fields.
- **Ansible inventory claims** — read `fleet-config/ansible/inventory/hosts.yml` directly:
  the six reachable groups (`gateway:lumen, inference:isis, db:sobek, git:anubis,
  workers:maat+bes`) and the `readonly` group (`thor, ptah, sekhmet, horus, neith, khnum,
  hathor`) match the doc's lists exactly. Confirmed `khepri`/`nephthys` are absent from the
  file entirely (`grep` for both names: zero hits).
- **fleet-exec-policy.yaml claims** — read the file directly: `ssh:` allowlist is at line
  192 as cited; `run_readonly_cmds` (line 204) has no `smartctl` entry (`true, uptime,
  hostname, uname, cat, ls, stat, df, du, free, systemctl ..., journalctl, ss, ip addr,
  tailscale status, docker ps, docker logs`); the "sudo is v2" comment is present right
  where the doc's citation says.
- **gatus/monitoring-role claim** — `fleet-config/docker/monitoring/` exists with a
  `docker-compose.yml` and a sibling `ansible/roles/monitoring/tasks/main.yml`, both
  referencing `gatus` — matches the "sibling service block, not a new stack" framing.
- **wq#2844/wq#3079 precedent claims** — `ops/wq2844-netdata-macs/install.sh` exists;
  `reports/wq3079_verify_wq2844_netdata_reachable_hathor_khepri.md` exists and its line 13
  confirms "Netdata 2.11.x apt package" — matches the doc's version citation verbatim.
- **External Scrutiny facts** — doctrine requires prior-art search before trusting claims
  about an unfamiliar tool; independently ran `fleet-search web` rather than taking the
  doc's own searx citation on faith: AnalogJ/scrutiny confirmed as a Golang hub+collector
  SMART-monitoring project (GitHub listing: "Hard Drive S.M.A.R.T Monitoring... Golang and
  Deployable via containers or binaries"), consistent with the doc's "small Go binary
  collector, no Docker required on the collected node" claim.
- **Unverified claims flagged honestly** — the doc flags two things as "not verified from
  this sandbox" rather than asserting them: whether `smartctl -a` works identically on the
  Apple AHCI blades, and whether netdata's `go.d.plugin` smartctl collector is already live
  on the fleet. Both are correctly hedged (`smartctl` is absent from the fleet-exec
  readonly-cmd allowlist, confirmed above), not asserted as fact.
- **Internal consistency** — the "not a Frank-gate" framing for Phase 2 (execution-boundary,
  not a pending decision) correctly applies instinct 3; the conditional-GO logic (gated on
  the netdata check, not on tool suitability) is carried through consistently from §"who can
  get a collector" into the verdict section without drifting.

## Falsifiability note

No code/test suite ships with this ticket — it's a research doc. The falsifiable probe here
is independently re-deriving the doc's checkable claims instead of trusting its transcription:
every `file:line` citation was opened and read, the drive count was recomputed from the raw
JSON rather than re-reading the doc's own table, the Ansible inventory and fleet-exec-policy
claims were grepped/read directly, and the external Scrutiny architecture claim was
re-searched from scratch. One gap surfaced (`wadjet` uncounted/unflagged); nothing else came
back contradicted.

## Verdict

**CONFIRMED.** Every verifiable factual claim in the doc — the AER category-error
correction, the drive-inventory arithmetic, the Ansible/fleet-exec-policy access-gate
claims, the gatus/netdata precedent citations, and the external Scrutiny architecture facts —
checks out against independent re-verification. The one gap found (`wadjet` omitted from the
drive count and from the "further undercount" flag alongside `thoth`/`apis`) doesn't change
the CONDITIONAL GO verdict or its phasing: wadjet would require Phase 2 regardless, for the
same inventory/allowlist reasons as khepri/nephthys. No evidence the verdict, phasing, or
access-gate reasoning are wrong.

No contradiction found; not escalating to judgment.
