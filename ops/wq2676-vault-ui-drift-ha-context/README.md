# wq#2676 — persistent ui-drift: ORIGIN_DOWN vault.lumennode.uk

## Ticket

ui-drift opened wq#2676 because `ORIGIN_DOWN vault.lumennode.uk
(http://192.168.8.178:8222)` survived 2 consecutive cycles. Prior attempt
(a1) failed as a PERMISSION_LOOP grepping `/home/lumen/sentinel/`, which is
outside this worker's allowed directories (see prior-attempt critique in the
ticket prompt).

## Investigation

1. Confirmed the outage is real, not stale: `curl -m5 http://192.168.8.178:8222`
   still returns `curl: (7) Failed to connect ... after 1 ms`.
2. Read `/etc/cloudflared/config.yml:76-81` — the `vault.lumennode.uk` ingress
   origin is `http://192.168.8.178:8222`, matching the drift report exactly.
   This is **not stale dashboard data**: `python3 /home/lumen/bin/ui-inventory
   --json` (regenerated fresh) reports the identical origin, so
   `ui-inventory --write-glance` would reproduce the same "down" state, not
   fix anything.
3. Read `/home/lumen/bin/wadjet-ha-monitor.sh` (Vault HA auto-promote
   monitor, runs on Lumen via systemd timer). It probes Ma'at (the normal
   vault primary) and on sustained failure fences Ma'at, promotes a standby,
   and flips the cloudflared origin for `vault.lumennode.uk` to the standby
   (`flip_origin_to_wadjet()`, `wadjet-ha-monitor.sh:97-101`).
4. Read `/home/lumen/ha/primary` (HA state file): contains `wadjet` — i.e.
   the standby role is the currently active vault primary right now.
5. Read `/home/lumen/ha/monitor.log`. Timeline:
   - `2026-08-31 12:16:26–31 UTC`: Ma'at failed its probe 17x, monitor
     fenced Ma'at and ran `do_failover()`, promoting the standby at
     `192.168.8.178` (the standby role was retargeted from the original
     Wadjet host to `sobek@192.168.8.178` on 2026-08-10, per the script's
     own `aq#429, Sobek retarget` comment at `wadjet-ha-monitor.sh:40-46`,
     and confirmed live in the log at line 568:
     `FAILOVER BLOCKED: standby sobek@192.168.8.178 not reachable ...`).
   - `flip_origin: vault origin now -> Wadjet` (line 608) — this is the
     exact write that produced the `192.168.8.178:8222` origin we see in
     `/etc/cloudflared/config.yml` today. **The config is correct and
     current for the HA state.**
   - From `2026-09-01` onward, repeated `WARN: Wadjet (current primary)
     probe FAILED` (e.g. lines 613–978, most recently continuous through
     `2026-09-24 12:57 UTC`, the day before this ticket opened). Auto
     **failback is intentionally not implemented** (`wadjet-ha-monitor.sh:6`)
     — a human has to run `wadjet-ha-failback.sh` once Ma'at (or a working
     standby) is available again.

## Conclusion

There is no dashboard/reality mismatch to fix here: `ui-inventory` and
`/etc/cloudflared/config.yml` both correctly reflect the HA monitor's last
successful failover. The underlying problem is that the promoted host
(`sobek`, LAN `192.168.8.178`) is itself down/unreachable, which the HA
monitor has been alerting on via ntfy since `2026-09-01` with no
auto-remediation by design. Restoring Vaultwarden requires either reviving
`sobek` or running a manual failover/failback to a reachable node — both
physical/live actions outside a sandboxed headless worker (no SSH key, no
root, `/home/lumen/ha/ha.env` and the SSH key path are explicitly denied
paths per guard.sh). This is the same class of genuine physical dependency
already logged for the same HA system in `kb/ISSUE-LOG.md` rows 31 (wq#325)
and 641 (wq#2448).

## What this ticket actually delivers

`ui-drift.diff` (this directory) — a proposed patch to
`/home/lumen/bin/ui-drift` (denied to direct Edit/Write for workers per
guard.sh: "Write/Edit inside PRIMARY tree /home/lumen/bin denied
(wq#1719/wq#900)") that makes the next occurrence of this exact situation
faster to triage: when an `ORIGIN_DOWN` finding's host is HA-managed (keyed
off `/home/lumen/ha/primary`, currently just `vault.lumennode.uk`), the
finding's `detail` string gets an appended
`[HA primary=<role> -- see <ha_dir>/monitor.log before filing as new drift]`
note. It does **not** suppress or downgrade the finding — ui-drift stays
read-only and still fires/notifies/tickets on every cycle exactly as before;
it only saves the next responder the ~half hour of `monitor.log`
archaeology this investigation took. Verified: `python3 -c "import ast;
ast.parse(...)"` on the patched copy, plus the `ha_context()` helper
exercised standalone against the real `/home/lumen/ha/primary` returning
`"HA primary=wadjet -- see /home/lumen/ha/monitor.log before filing as new
drift"` for `vault.lumennode.uk` and `None` for an unmanaged host
(`plex.lumennode.uk`).

Applying `ui-drift.diff` and reviving `sobek`/running the manual failback
are physical/repo-write tasks outside this worker's reach and are not
gated on Frank — they're queued as follow-up, not blocking this ticket.
