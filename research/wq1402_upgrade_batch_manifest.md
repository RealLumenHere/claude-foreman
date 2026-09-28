# wq#1402 upgrade batch manifest (prep: wq#2914)

Read-only research, 2026-09-28. Nothing was upgraded, restarted or queued by this document.
Versions were read live on the nodes; latest-stable comes from the upstream GitHub releases API
(2026-09-28). Evidence grades: **A** vendor page read directly, **B** third-party mirror of a
vendor record (GitLab GLAD, OSV, OpenCVE, Debian tracker), **C** not found.

## How lumen-window runs an op (read from lumen_window.py, not assumed)

- `cmd`, `verify` and `precheck` run as `bash -c` **locally on Lumen**. `--node` is only a gate label
  (and the reboot-gate target). A remote op has to `ssh` inside its own cmd.
- A failed cmd or verify halts the batch, sends an urgent ntfy and leaves the op queued
  (attempts+1). **There is no automatic rollback.** Rollback must live inside the op script.
- `control-plane` tagged ops are never run; the drain lists them and Frank drives them.
- Fleet window Mon 02:00-05:00 PT, margin 600 s, verify 300 s. Next windows: Mon 2026-10-05,
  Mon 2026-10-12 (both PDT). Thor is excluded from everything.
- Consequence: a tailscale/cloudflared op that ssh's over the tailnet to the node being restarted
  loses its own session. Those ops need a LAN second path and a detached remote step.

## Summary

| item | running | latest stable | advisories that matter | action |
|---|---|---|---|---|
| LiteLLM (Lumen) | 1.83.10 (digest 6c82d338) | v1.103.0 | **GHSA-4xpc-pv4p-pm3w Critical** (A) | window 1, patch line |
| Tailscale | 1.98.4 wadjet; 1.98.10 sia; 1.102.2 most; 1.102.3 hathor; 1.102.4 khepri | v1.102.4 | TS-2026-004..009, 010, 011 (A) | window 1, node by node |
| cloudflared | Lumen 2026.7.3; Bes 2026.8.2 | 2026.9.3 | none found (C) | control-plane, Frank drives |
| Home Assistant (Bes) | 2026.7.2 | 2026.9.4 | ticket's HIGH not located (C) | window 2 |
| Ollama | 0.32.6 (hathor 0.30.7) | v0.34.4 | third-party only (B) | window 2 |
| netdata | 2.11.1 (thoth 2.11.0) | v2.11.1 | already fixed (B) | drop from batch |
| n8n (Lumen) | 2.32.7 | 2.40.7 | already fixed (B) | drop from batch |
| esphome (build image) | 2026.6.5 | 2026.9.0 | none for our range (B/C) | defer |

The ticket's 2026-09-03 targets are stale: cloudflared 2026.8.3 is now 2026.9.3, Tailscale 1.102.3 is
now 1.102.4, Ollama 0.33.2 is now 0.34.4.

## 1. LiteLLM (Lumen, docker compose /home/lumen/docker/docker-compose.yml, service litellm)

- Live: 1.83.10, image pinned by digest sha256:6c82d338a60e7b273ae46bf1d8db814d2856ae010f96c44eeadde574d3893f76.
  Bound 127.0.0.1:4000, also served on the tailnet via `tailscale serve`.
- **GHSA-4xpc-pv4p-pm3w / CVE-2026-49468, Critical (CVSS v4 9.5):** Host-header auth bypass on management routes.
  Fixed in 1.84.0; backported as `v1.83.10-stable.patch.3`. Source (A): docs.litellm.ai/blog/host-header-auth-bypass.
  The advisory says it only bites under three conditions; those conditions have NOT been read against our
  setup yet (gap G1). Whether our pinned digest already includes patch.3 is unverified.
- GHSA-hx8v-g79f-8w5f (Moderate, user_config SSRF): fixed 1.83.9 per GitLab GLAD (B). Not affected.
- GHSA-3cv6-jpf6-8222 (Moderate, authenticated SSRF and provider-credential exfiltration): fixed only in
  1.88.6 and 1.96.2 per the CVE record via OpenCVE (B). **The 1.83 line has no fix.** Needs a minor-version
  jump with its own config-break research (gap G2).
- Registry check: `ghcr.io/berriai/litellm:v1.83.10-stable.patch.3` and `:v1.96.2` exist. `main-v1.103.0` does not.
- Plan: step 1 = patch line (fixes the Critical, same config surface). Step 2 = >=1.96.2, separate ticket.
- Precheck: canary the new image on 127.0.0.1:4001 with the same config and env_file, hit /health/readiness.
  Dump DB `litellm` on lumen-pgvector first (spend logs; a patch may run migrations). Back up the compose file.
- Upgrade: resolve the patch.3 digest, pin it in the compose file, `docker compose up -d litellm`.
- Verify: readiness healthy + db connected + an authenticated /v1/models call. No stable smoke key exists yet (gap G3).
- Rollback: restore the compose backup (old digest above), `docker compose up -d litellm`; restore DB only if a migration ran.
- est_sec ~300. Restarts the gateway that every Minerva and fleet LLM call uses.

## 2. Tailscale (apt, pkgs.tailscale.com/stable, candidate 1.102.4 on nodes checked)

- Official bulletins (A, tailscale.com/security-bulletins):
  - TS-2026-004, -005, -006, -007, -008, -009: fixed in **1.98.9**. Only **wadjet (1.98.4)** is below.
  - TS-2026-010 (Tailscale SSH acceptEnv leak): fixed 1.102.1. sia (1.98.10) is below; matters only if acceptEnv is used.
  - TS-2026-011 (4via6 subnet routers only): fixed 1.102.3. Unverified whether any node advertises 4via6.
- Nodes: anubis, bes, isis, maat, nephthys, sobek, thoth, lumen 1.102.2; hathor 1.102.3; khepri 1.102.4 (done);
  sia 1.98.10; wadjet 1.98.4. apep, gameboy, seshat are tailnet-offline (15, 14, 9 days) and skipped. Thor excluded.
- Order: wadjet first (only real exposure), sia, then the 1.102.2 group one at a time, **Lumen last and control-plane**.
- Second path: LAN IP reachability from Lumen before each node (known for most nodes; **wadjet has no LAN IP recorded**, gap G4).
- Upgrade: `sudo apt-get install -y --only-upgrade tailscale`, run detached on the node (systemd-run --wait) so a
  tailscaled restart cannot kill dpkg. Drive it over the LAN IP, not the tailnet IP.
- Verify: `tailscale version` == 1.102.4, BackendState Running, `tailscale ping` from Lumen, ssh over the tailnet IP.
- Rollback: `apt-get install tailscale=<previous>` if the old version is still in the repo, else stop and alert.
- est_sec ~90 per node. Never while agent sessions are live on the mesh: the op script must check `wq` for active sessions.

## 3. cloudflared (apt, pkg.cloudflare.com, candidate 2026.9.3) - control-plane

- Lumen 2026.7.3 -> 2026.9.3. Official release notes (A) list fixes (e.g. TUN-10738 registration error
  propagation, Go toolchain bump) but no CVE or GHSA ids. The ticket calls it highest exposure; no advisory backs that.
- Tag `control-plane`. Frank drives it, detached via systemd-run (Commander rides this tunnel).
- Backup: copy /usr/bin/cloudflared to a dated file before upgrading (the apt repo may not keep 2026.7.3).
- Verify: `curl -sI https://vault.lumennode.uk | head -1` is 302 (baseline from handoff verify) and a Commander MCP call succeeds.
- Rollback: copy the saved binary back, `systemctl restart cloudflared`.
- **Bes finding:** cloudflared.service on Bes has NRestarts=119486 (about 15 s cycle for ~3 weeks), "control stream
  encountered a failure", version 2026.8.2. Not part of this batch. Ticket separately (F1).

## 4. Home Assistant (Bes, /home/bes/stack/compose.yaml, service homeassistant, image :stable)

- Live 2026.7.2, image digest sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b, config in /home/bes/stack/homeassistant.
- Latest 2026.9.4. That is two minors; release-note breaking changes for 2026.8 and 2026.9 not read yet (gap G5).
  A GitHub issue thread (C) says 2026.9 ends InfluxDB YAML config; precheck: grep the config for influx.
- The ticket's HIGH advisory was not located in home-assistant/core sources. Do not claim one.
- Backup: stop the container, tar the config dir (sqlite consistency), then upgrade.
- Upgrade: `cd /home/bes/stack && docker compose pull homeassistant && docker compose up -d homeassistant` (recreates only HA).
- Verify: HTTP 200 on :8123 and an E-plug read through eplug-fetch.py (HA REST). Rollback: restore tar, pin the digest above.
- est_sec ~240.

## 5. Ollama

- Lumen, isis, nephthys 0.32.6; hathor 0.30.7. Latest v0.34.4 (2026-09-23). Binary /usr/local/bin/ollama, unit ExecStart `ollama serve`,
  Lumen env: OLLAMA_MODELS=/scratch/ollama-models, KEEP_ALIVE=30s, MAX_LOADED_MODELS=1, NUM_PARALLEL=1.
- Advisories: only distro-repackaged records (B, CleanStart) citing CVE-2026-46603 and earlier batches; their version numbers
  are packaging versions, not upstream. Treat as "may be affected", verify against upstream (gap G6).
- Do not run the vendor install script blind: it rewrites the systemd unit and can drop the env above. Back up unit + drop-ins,
  swap the binary from the release tarball, diff the unit after. Exact procedure not yet checked against vendor docs (gap G6).
- Hathor had model pulls ("bakeoff") 6-7 hours before this probe: check idle before touching it.

## 6. netdata, n8n, esphome - not in the batch

- **netdata:** CVE-2026-83599 fixed 2.11.0; CVE-2026-83597/98/600 fixed 2.10.4 (B; GHSA-3mxw-fv2x-rhc6 for the last).
  Every node that reports a version is >=2.11.0. hathor, khepri, nephthys, sia, wadjet returned no version: confirm installed or not (gap G7).
- **n8n 2.32.7** (npm global, n8n.service + n8n-watcher.service, user lumen): CVE-2026-72765 / GHSA-gv7g-jm28-cr3m High 8.7 fixed in
  2.31.5 and 2.32.1 (B). Already fixed. Latest 2.40.7 is optional. **n8n listens on *:5678**; off-box reachability unchecked (F2).
- **esphome:** Lumen has the docker build image esphome/esphome:latest at 2026.6.5 (unpinned tag). It is a build tool; production
  firmware changes only when a plug is reflashed. Plug E1 (powers gameboy) is offline. 2026.9.0 is out with breaking changes
  (custom eFuse MAC, modbus). No advisory found for 2026.6.5. Defer until a real advisory or gameboy is back.

## Windows

- Window 1 (Mon 10-05, <=3 h): LiteLLM patch (~300 s), Tailscale nodes (~90 s each, ~15 min total), then Frank runs cloudflared + Lumen tailscale.
- Window 2 (Mon 10-12): Home Assistant (~240 s), Ollama per node (~120 s each).
- Total estimated under 1 h per window plus margins.

## Proposed queue commands - DO NOT QUEUE YET

None can be queued as bare commands: each needs an op script with LAN precheck, detached remote step, verify and embedded rollback,
tested to failure. Proposed shape once `/home/lumen/bin/maint-ops/*` exist:

```
lumen-window queue add --id wq1402-litellm-patch3 --window fleet --node lumen --est-sec 300 --ticket 1402 --pri 1 \
  --precheck '/home/lumen/bin/maint-ops/litellm-upgrade --canary' \
  --cmd '/home/lumen/bin/maint-ops/litellm-upgrade --apply' --verify '/home/lumen/bin/maint-ops/litellm-upgrade --verify'
lumen-window queue add --id wq1402-ts-wadjet --window fleet --node wadjet --est-sec 90 --ticket 1402 --pri 1 \
  --cmd '/home/lumen/bin/maint-ops/tailscale-upgrade wadjet' --verify '/home/lumen/bin/maint-ops/tailscale-upgrade --verify wadjet'
# repeat per node in the order above; lumen and cloudflared carry --tags control-plane
lumen-window queue add --id wq1402-ha-bes --window fleet --node bes --est-sec 240 --ticket 1402 --pri 2 \
  --cmd '/home/lumen/bin/maint-ops/ha-upgrade' --verify '/home/lumen/bin/maint-ops/ha-upgrade --verify'
```

## Gaps (each becomes a ticket)

G1 read the three exposure conditions of GHSA-4xpc-pv4p-pm3w against our listener; confirm whether our digest is patch.3.
G2 research the >=1.96.2 jump (config-break) for GHSA-3cv6-jpf6-8222.
G3 stable authenticated LiteLLM smoke key/tool for verify.
G4 wadjet LAN IP / second path.
G5 HA 2026.8 + 2026.9 breaking-change review.
G6 Ollama upstream advisories and vendor-doc upgrade procedure.
G7 confirm netdata presence on the five silent nodes.
F1 Bes cloudflared crash loop. F2 n8n bound to *:5678.
