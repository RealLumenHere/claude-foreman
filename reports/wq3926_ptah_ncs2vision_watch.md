# wq#3926: ptah ncs2-vision container unhealthy watch

## Ticket ask

docker ps on ptah showed `ncs2-vision Up 2 days (unhealthy)`, started
2026-10-12T11:00:07Z, healthcheck `GET 127.0.0.1:8090/health` returning
HTTP 503, FailingStreak 7412. Predates the 2026-10-15 02:39 UTC apt
upgrade. Asked: `docker logs ncs2-vision --tail 100` (read-only), check
NCS2 USB enumeration (`lsusb` for 03e7), whether the MYRIAD device
dropped. No container restart without Frank/backup per change-safety.

## Method and finding: host itself is unreachable

Before any container-level check, re-verified ptah's own reachability
rather than trusting the stale `claude_memory` "healthy" note (per the
fabricated-evidence instinct — confirm against the fleet, don't assume):

- `/home/lumen/bin/lumen_state.py current ptah` reports
  `ptah.availability = offline — did not return from Frank-approved
  reboot at 2026-10-15 02:54 UTC ... No ping on LAN 192.168.8.125 (ARP
  FAILED), no tailnet, 7+ min. No smart plug assigned to ptah, so no
  remote power control. Needs physical check.`
- Live re-probe from this worker: `/home/lumen/bin/fleet-exec run ptah
  'uptime'` → `ssh transport error to ptah: ssh: connect to host
  100.78.207.56 port 22: Connection timed out`.

ptah does not answer SSH on either its LAN address (192.168.8.125) or
its tailnet address (100.78.207.56). This means `docker logs
ncs2-vision`, `lsusb`, and any MYRIAD/NCS2 USB-enumeration check are
all unreachable from this session — there is no remote session on
ptah to run them from. The host went dark after the 2026-10-15 kernel
upgrade reboot, which is a separate and larger problem than the
ncs2-vision healthcheck this ticket was filed against.

## Verdict

Cannot collect the requested container evidence (logs, lsusb, MYRIAD
enumeration) because the host is offline, not because of a gate. No
container restart is possible either way (none was authorized, and
none is reachable). The original 503/FailingStreak-7412 unhealthy
state — dated 2026-10-12, i.e. three days before this reboot — remains
unexplained pending host recovery.

## Memory correction

`claude_memory` lists ptah ncs2-vision as healthy. That note is stale
on two counts: the container was already unhealthy since 2026-10-12,
and the host itself has been offline since the 2026-10-15 02:54 UTC
reboot. Logged as ISSUE-LOG row 1354
(`/home/lumen/kb/ISSUE-LOG.md`) rather than editing claude_memory
directly, per the proposal-only memory-consolidation instinct.

## Physical task queued (not a Frank-gate)

ptah has no smart plug, so nobody can power-cycle or inspect it
remotely. Per the full-autonomy instinct, this is the one legitimate
exception — a physical act nobody else can perform — so it is queued
as a physical task (walk over, check power/link lights, re-seat the
NCS2 USB stick, re-apply power) rather than left as a blocking
question. Once ptah is physically back online, re-run
`docker logs ncs2-vision --tail 100` and `lsusb | grep 03e7` to
determine whether the Myriad device dropped or the process itself
wedged.

## Cross-references

- ISSUE-LOG row 1354 (`/home/lumen/kb/ISSUE-LOG.md`)
- wq#3866 (apt upgrade patch that preceded the reboot ptah has not
  returned from)
