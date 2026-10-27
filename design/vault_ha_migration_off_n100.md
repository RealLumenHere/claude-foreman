# Vault HA migration off Maat/Sobek/Bes (pre-N100-decommission) — wq#4679

Status: DESIGN ONLY — no execution in this ticket.
**Maat decommission is GATED on (a) this design landing and (b) a Frank-present cutover window.**

## 1. Current state (verified 2026-10-27)

| Node | HW | Role | Address |
|---|---|---|---|
| Maat | WeiBu ADL-N, Intel N100, 16GB | PG18 PRIMARY + serving Vaultwarden (docker, 1.36.x) | LAN 192.168.8.202, TS 100.105.219.34, ssh user `maam` |
| Sobek | Trigkey Green G4 N100, 16GB | PG18 streaming standby (`sobek_slot`, lag 0) + parked VW payload; **current auto-promote target** | LAN 192.168.8.178, TS 100.86.156.91 |
| Bes | Trigkey Green G4 N100, 16GB | PG18 streaming standby (`bes_slot`) + parked VW payload; also Home Assistant, mosquitto, Grafana (power.lumennode.uk) | LAN 192.168.8.177, TS 100.110.156.91 |
| Lumen | — | cloudflared tunnel `lumen-node` (public ingress vault.lumennode.uk), `wadjet-ha-monitor.timer` (30s, ARMED), `/home/lumen/ha/` state, `vw-bes-datasync.timer` | — |

- Public origin: `/etc/cloudflared/config.yml:76-77` → `http://100.105.219.34:8222` (Maat VW, TS bind only; LAN 8222 refused by design).
- bw-CLI endpoint: `https://maat.tail12fa3b.ts.net` via `tailscale serve` (bw ≥2025.11 rejects http://).
- PG data: `/var/lib/postgresql/18/main` on all three (PG **18** — targets must match major version).
- VW split storage: ciphers/users/devices live in PG db `vaultwarden` (replicates via streaming); file assets (`rsa_key.pem`, attachments, icon cache) + `vw.env` (ADMIN_TOKEN) live in `/opt/<node>-vaultwarden/{data,vw.env}`, synced hourly Maat→Bes by `vw-bes-datasync.timer` on Lumen (wq#324 pattern).
- Maat input firewall (wq#3108): INPUT DROP; 5432 accepted only from Bes .177, Sobek .178, Lumen .160 — **a new replica's IP must be added before basebackup can connect**.
- `fleet-plan.target_fleet` (Frank 2026-10-24): 7 systems — PB63 14500T 64GB (new Lumen), AG3 (new Thor), Seshat, M70q (Limpet), i3-14100 TBD (services), DXP4800 GT Ryzen 32GB (storage), Anubis (Gitea+Borg). Decommission: Maat, Bes, Neith, Khnum, old Lumen, Horus, Macs, Apis, Ptah, Sekhmet, NDS. **Sobek is in neither list** → it is the natural bridge standby and the last N100 standing.

## 2. Hard rules (compression-fragile — copied, not paraphrased)

HANDOFF.md §4, verbatim:

> - SAFE SWITCHOVER (mandatory): (1) DISARM monitor (stop wadjet-ha-monitor.timer + rm AUTO_PROMOTE_ENABLED); (2) run DETACHED: `sudo systemd-run --unit=vault-switchover --collect bash /home/lumen/bin/wadjet-ha-failback.sh switchover --confirm` (Commander rides the SAME cloudflared it reloads, so a foreground run gets SIGKILLed mid-script → outage); (3) poll `journalctl -u vault-switchover` after ~60s blip; (4) verify, THEN re-arm.
> - pg_basebackup copies the source's postgresql.auto.conf → target inherits WRONG listen_addresses (binds only localhost). After any basebackup+promote, fix the node's listen_addresses to its OWN IP.
> - Ma'at login user = `maam` (not maat). maam has PASSWORD sudo (not NOPASSWD); Wadjet has NOPASSWD.

Additional standing constraints:
- Never SIGHUP cloudflared — it exits on SIGHUP (wq#3575); origin flips go through `/home/lumen/bin/cfd-actuator request --kind reload` (detached restart), exactly as `wadjet-ha-failback.sh:174-176` does.
- Vault/cloudflared changes only via detached systemd-run (instinct 6).
- STANDING SAFETY (HANDOFF.md): back up before any DB/config change; verify replication lag=0 before promote/switchover; never single-node without a confirmed standby.
- Pre-scan source PGDATA for non-postgres-owned files before any basebackup (`wadjet-ha-failback.sh:52-66` — the 2026-06-26 root-owned-file data-loss window) — the target dir is wiped *before* the copy can fail.
- ISSUE-LOG #847 (split brain): never trust "ARMED" without checking the timer is active; re-arm only after the `standby_promotable` gate passes against the rebuilt standby.

## 3. Target host options

| Option | For | Against |
|---|---|---|
| **i3-14100 services node (RECOMMENDED primary)** | Matches Maat's exact role (dedicated PG+VW box); keeps the crown jewels off the busiest node; preserves the fence model — Lumen owns the CF origin, the monitor on Lumen probes an *independent* host; 14100 ≫ N100 | Hardware is "TBD" in the plan — this migration becomes its first workload; needs purchase/provisioning first |
| PB63 new Lumen | Always-on, 64GB, zero cross-node hop to cloudflared | Monitor, ingress, fence, and primary collapse onto one host — a Lumen outage then takes the vault *and* the failover machinery with it; busiest node = biggest blast radius; new-Lumen bring-up will be churny for weeks |
| DXP4800 GT (storage) | Ryzen + 32GB is ample; always-on by role | NAS firmware/OS may fight a native PG18 systemd deployment; mixes bulk storage I/O with crown-jewel DB; better used as the *standby* + backup target |

**Recommendation: primary = i3-14100 services node; first new-fleet standby = DXP4800 GT; Sobek stays as bridge standby throughout.** If Frank wants to decommission faster than the i3-14100 arrives, PB63 is the acceptable fallback primary — but the i3 box should still take the role when it lands.

## 4. Migration sequence (all execution OUTSIDE this ticket; cutover Frank-present)

PG on the target must be the same major (18) — streaming replication and basebackup do not cross majors.

### Phase 0 — provision target (no vault impact)
Ubuntu LTS, static LAN IP, Tailscale (tag:lumen-fleet), key-only sshd, NOPASSWD sudo for the HA path (avoid Maat's password-sudo friction), PG18 installed and stopped, docker. Add target LAN IP to Maat's iptables 5432 accept list and to Maat `pg_hba.conf` (`host replication replicator <target>/32 scram-sha-256`); persist via iptables-persistent.

### Phase 1 — backup/restore proof (GATE for everything below)
Reuse the #847 proof pattern: `pg_dump -Fc vaultwarden` from Maat → `/home/lumen/backups/vault-migration-<ts>/` (0600) → restore into a scratch db on the target → row-count + content diff (users/ciphers/devices/folders byte-identical modulo `\restrict` tokens). Also tar-sync `/opt/maat-vaultwarden/data` + `vw.env` to the target and checksum `rsa_key.pem` both sides. **No cutover until this restore demonstrably works on the target.** Final pre-cutover dump archived to Anubis/Borg.

### Phase 2 — target becomes third standby
Reuse `bin/vault-sobek-rebuild.sh` mechanics (it is the target-shaped generalization of the failback script): pre-flight dumps of source+target, source PGDATA prescan, idempotent `pg_create_physical_replication_slot('<target>_slot')` on Maat, `pg_basebackup -h 192.168.8.202 -U replicator -D /var/lib/postgresql/18/main -R -S <target>_slot -X stream`, then **pin `listen_addresses = 'localhost,<target-LAN-IP>'` in postgresql.auto.conf while PG is stopped, before first start** (`wadjet-ha-failback.sh:68-90` — the inherited-listen_addresses pitfall). Verify `pg_is_in_recovery()=t`, streaming in Maat's `pg_stat_replication`, lag 0.

### Phase 3 — VW payload parked on target (wq#324 profile)
Clone container config (image pin, host-net, `DATABASE_URL=postgresql://…@127.0.0.1:5432/vaultwarden`, `DOMAIN=https://vault.lumennode.uk`, same ADMIN_TOKEN) into `/opt/<target>-vaultwarden/vw.env` (0600); `restart=no`. Repoint the hourly datasync timer (new `vw-<target>-datasync` on Lumen, PRIMARY-GUARDED like `vw-bes-datasync.sh`). Start-test: expect PG auth success then `ReadOnlyTransaction` panic at diesel migrations — that panic IS the pass signal on a read-only standby. Set up `tailscale serve --bg --https=443 → :8222` on the target for the bw CLI.

### Phase 4 — CUTOVER (Frank present; SAFE SWITCHOVER §2 verbatim, adapted targets)
A new `vault-cutover.sh` (adapted from `wadjet-ha-failback.sh switchover`, lines 157-191, with Maat as source and the new node as promote target), run **detached**: `sudo systemd-run --unit=vault-switchover --collect bash /home/lumen/bin/vault-cutover.sh --confirm`.
1. DISARM monitor (stop `wadjet-ha-monitor.timer` + rm `AUTO_PROMOTE_ENABLED`).
2. Detached unit: stop Maat VW (halt writes) → verify replay lag 0 (abort >16MB, same gate as `wadjet-ha-failback.sh:164-166`) → `pg_promote()` on target → re-pin target listen_addresses → flip `/etc/cloudflared/config.yml` vault.lumennode.uk origin to `http://<target-TS-IP>:8222` (`.bak` first) → `cfd-actuator request --kind reload` (never SIGHUP) → start target VW (diesel migrations now run read-write).
3. Poll `journalctl -u vault-switchover` after the ~60s blip.
4. Verify: `/alive` 200 via vault.lumennode.uk, `bw login`/sync against the new https endpoint, attachments fetch, `handoff verify` shows exactly one `in_recovery: f`.
5. THEN re-arm (phase 5). Do not re-arm in between.

### Phase 5 — repoint HA machinery, re-arm
Update `/home/lumen/ha/ha.env`: MAAT_* → new primary, WADJET_* (compat alias for the promote target) → chosen standby (Sobek initially); `echo <target> > ha/primary`; reset fail count. Rebuild Sobek as standby *of the new primary* (same Phase-2 mechanics; new slot). Re-arm only after the `standby_promotable` gate passes (reachable AND `in_recovery=t`) and the timer is verified **active** (#847 trap). Retire `vw-bes-datasync` in favor of the new datasync.

### Rollback (window stays open until Maat is wiped)
Do NOT wipe Maat at cutover. Immediately after promote, rebuild Maat as a standby of the new primary (rebuild-maat mechanics, source/target swapped) and keep it streaming through a ≥7-day soak. Rollback = reverse switchover: stop new VW → promote Maat → restore `config.yml` from `.bak` → cfd-actuator reload → start Maat VW → repoint ha.env back. CF config backups and the Phase-1 dump make this a <10-minute path.

## 5. Decommission order (which N100 goes when)

1. **Bes first** — once the target standby streams at lag 0, Bes's vault duty is redundant. (Blocker: Home Assistant, mosquitto, and Grafana/power.lumennode.uk must be rehomed first — separate ticket, not vault work.)
2. **Maat second** — only after the Frank-present cutover + soak + final dump archived to Anubis/Borg, and only with this design landed (the stated gate). Physical notes: UPS plug P4, BIOS restore-on-AC set.
3. **Sobek last** — not on the decommission list; it remains the second standby and auto-promote target until the DXP4800 GT (or PB63) standby is proven `standby_promotable`, then retires whenever Frank chooses. **Answer to the ticket question: Sobek is the last N100 to go.**

Neith/Khnum are RK3399 ARM boxes (Commander standbys), not vault nodes — no vault work blocks their decommission.

## 6. Open items for the execution tickets
- Purchase/provision i3-14100 (or Frank picks PB63-as-primary fallback) — physical, Frank.
- Write `vault-cutover.sh` + updated monitor env (code ticket; adapt, don't fork-and-drift: keep `prescan_source`/`fix_listen_addresses` semantics).
- Rehome Bes's HA/MQTT/Grafana (separate ticket; blocks Bes, not the vault).
- Update `maat.vault_https_endpoint` consumers (bw CLI configs) to the new `tailscale serve` hostname at cutover.
