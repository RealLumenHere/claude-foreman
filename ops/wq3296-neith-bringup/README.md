# wq#3296 — P1-A (neith) bring-up: Commander backup + fleet-memory replica

Design FINAL: `agents/work-queue/reports/p1pro_role_assignment_design.md` (wq#3261,
locked 2026-09-30). Roles 1a + 2: Commander BACKUP (`p1-commander --role backup`)
plus the pull-based fleet-memory replica. Nothing live depends on neith; the B
(ptah) cutover is a separate Frank-driven phase and is NOT touched here.

## Status at prep time (2026-10-01)

**BLOCKED ON PHYSICAL PREREQ.** No neith/p1-backup subject in lumen_state, no
Station P1 on the tailnet (re-confirmed by wq#3293 on 2026-10-01), and no record
that Frank has confirmed the three units on hand or flashed A with Balena Etcher
(the design's Frank-physical list, items 1-2). Everything in this kit is staged
so bring-up is a checklist replay once the flashed board boots.

The physical part is wq#3334; Frank's steps are `BENCH-CARD-wq3334.md` in this
directory, ending with the `wq done 3334` that trips this ticket's
`Liveness: ticket_done(3334)` gate.

## Bring-up order (run once neith answers DHCP)

1. Follow `foreman/ops/p1-commander/CHECKLIST.md` "Per box" top section
   (first-run wizard, `fleet-provision-ssh`, static LAN IP via netplan).
   Hostname is **neith** (design decision 4): everywhere the wq#2744 kit says
   `p1-backup`, rename first:
   `sed -i 's/p1-backup/neith/g' <kit-copy>/*.service <kit-copy>/*.yml <kit-copy>/install.sh`
   (work on a copy of the kit, not the canonical one — ptah still needs the
   p1-primary names).
2. **Verify hardware, do not assume** (design: RAM/NIC/disk/thermals are
   unverified): `free -h`; `ethtool eth0 | grep Speed` (1G link on a 2.5G core
   port is expected if the NIC is 1GbE — NOT the wq#350 downshift fault);
   `lsblk -d -o NAME,SIZE,MODEL,ROTA` + `smartctl -H` on the eMMC/SD;
   `cat /sys/class/thermal/thermal_zone*/temp` at idle and under
   `stress-ng --cpu 4 -t 60`. Record all four in lumen_state (step 3).
3. Ingest to lumen_state from Lumen (LAN IP from fleet-inv, never invented):
   `lumen_state.py ingest neith hardware=<model/ram/disk> lan_ip=<fleet-inv ip> tailscale_ip=<ts ip>`
   then `state-regen`.
4. Host firewall, stage 1 of the design (no Topton yet): wq3108 standard.
   From `foreman/ops/wq3108-fleet-input-default-drop/`: run `genrules.py` for
   neith (INPUT DROP; lo, RELATED/ESTABLISHED, ts-input, LAN ssh from
   192.168.8.0/24, icmp) and apply ON neith with the dead-man:
   `sudo systemd-run --unit wq3108-apply-neith bash apply.sh arm <staged-dir>`,
   verify tailnet ssh + LAN ssh from Lumen, then `apply.sh disarm`.
   Outbound per design stage 1: restrict egress to tcp/443 + udp/41641
   (cloudflared, tailscale) — add the OUTPUT rules to the staged.v4 before arm.
5. Commander backup kit: `sudo ./install.sh --role backup --hostname neith`
   from the renamed kit copy, then the cloudflared steps in CHECKLIST.md
   (tunnel login/create are **[FRANK]** — browser auth).
   The 2-week code-lag window machinery already exists on Lumen
   (lumen-window-open/close-commander-backup timers, next 2026-10-07 09:00 UTC;
   p1-update-gate.sh is read-only/alert-only).
6. Memory replica (role 2, this kit): `sudo ./install-replica.sh` ON neith.
   See "Replica design" below. Then force one pull + one restore test:
   `systemctl start neith-memory-pull.service neith-restic-restore-test.service`
   and check `/srv/fleet-memory/status/` for both OK files.

## Replica design (locked by wq#3261)

- **DATA only, pull-based, neith initiates.** Code lags 2 weeks; data must not.
- Sources on Lumen (paths verified against repo references at prep time):
  `/home/lumen/minerva/minerva.db` (sqlite — also holds the lumen_state store:
  `state_facts`, 1691 rows at prep time), `/home/lumen/agents/work-queue/queue.db`
  (sqlite), `/home/lumen/claude_memory.md` + its archive — no standalone
  archive file exists (checked `~`, `~/kb`, `~/memory`, `~/archives` at prep);
  the history lives in ~51 dated `claude_memory.md.bak.*` siblings (23MB
  total), so the export globs `claude_memory*` and takes them all —
  `/home/lumen/HANDOFF.md`, `/home/lumen/instincts.yaml`.
- Sqlite files are snapshotted with `sqlite3 ... ".backup"` on Lumen
  (`lumen-export-memory-snapshot.sh`, invoked BY neith over ssh), never copied
  live. Neith then rsyncs the staging dir — still pull-based: neith drives
  both steps on its timer (hourly; cheap to retune, "fresh" per design).
- Restic repo lives on neith (`/srv/fleet-memory/restic`), password file
  generated 0600 at install time, never committed. Weekly restore test
  restores latest snapshot to a tmpdir, runs `PRAGMA integrity_check` on both
  restored DBs, and checks the required file set; result lands in
  `/srv/fleet-memory/status/restore-test.json` (and the journal). Lumen-side
  monitoring of that file is follow-up work, noted as such.
- **Single writer**: Lumen (later ptah) writes, neith's copy is read-only
  (staging owned by the pull user, mode 0750 root of tree). Promotion is a
  manual runbook step, never automatic.
- Excluded (design): secrets beyond what Commander needs, model weights,
  anything that would make neith a second Lumen.

## Files

| file | runs on | what |
|---|---|---|
| `lumen-export-memory-snapshot.sh` | Lumen (ssh-invoked by neith) | sqlite .backup + file copies into `~/fleet-memory-staging/` |
| `neith-memory-pull.sh` | neith | invoke export, rsync staging, restic backup, prune |
| `neith-memory-pull.service` / `.timer` | neith | hourly pull |
| `neith-restic-restore-test.sh` | neith | weekly verified restore (integrity_check + file set) |
| `neith-restic-restore-test.service` / `.timer` | neith | weekly schedule |
| `install-replica.sh` | neith | dirs, restic init, units, timers |
