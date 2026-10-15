# wq#4011: OPNsense rewire prep — subnet record reconciliation, HA disarm gate, WAN topology

Source: `scratch/research/opnsense-post-rewire.md` (session emerging-research-19b71e,
2026-10-14, research only — nothing on the fleet or OPNsense was changed by that session or
this one). Cross-ref: `research/NETWORK_PREMORTEM.md` (wq#2483). This doc does not touch
live network state; it reconciles written records and documents a gate that didn't exist
in the repo before this ticket.

## 1. Subnet record reconciliation

Four records disagree about two addresses. Laid side by side:

| record | value | source |
|---|---|---|
| OPNsense LAN (current, pre-rewire) | `192.168.1.1` | lumen_state handoff note (scratch doc §0.5) |
| friend's TP-Link Archer BE550 | `192.168.1.0/24` | FLEET_MAP (external fleet inventory, not this repo) |
| friend's TP-Link Archer BE550 | `192.168.0.1` | claude_memory (external, not this repo) |
| be3600 workbench router LAN | `192.168.0.1` | claude_memory + scratch doc §0.5 |

**Resolution:**

1. **claude_memory's TP-Link address (`192.168.0.1`) is the stale record.** It collides
   exactly with be3600's independently-recorded LAN address, and no doc describes the two
   boxes sharing a broadcast domain. The far more likely explanation is a conflation: a note
   written about the workbench be3600 got mis-attributed to the TP-Link. FLEET_MAP's
   `192.168.1.0/24` for the TP-Link is the one to trust — a `/24` grant for a named device
   (vs. a single recalled IP) reads as the deliberately-recorded value, not a guess.
2. **That makes the real conflict a subnet collision, not a transcription error.**
   OPNsense's current (factory-default, pre-rewire) LAN — `192.168.1.1` — sits in the exact
   same `/24` as the friend's TP-Link. This is the overlap scratch-doc §0.5 and §1 already
   flagged and already designed around: the rewire plan re-IPs OPNsense's LAN to
   `192.168.8.1/24` (taking over the existing fleet gateway address, `research/NETWORK_PREMORTEM.md:100`
   confirms every LAN address the fleet's own tooling uses today is already in
   `192.168.8.0/24`) specifically so OPNsense never goes live on `192.168.1.0/24`. No further
   action is needed here beyond confirming the plan — the collision is real, but it is a
   pre-rewire factory-default state, not a target-state conflict, and the target state
   (`192.168.8.1/24`) already avoids both `192.168.0.0/24` (be3600) and `192.168.1.0/24`
   (TP-Link).
3. **RAM discrepancy (16 GB in lumen_state vs. 32 GB in FLEET_MAP's "Network Guardian"
   entry) is not resolvable from here.** Neither record cites a method (invoice, `dmidecode`,
   a BIOS screen), the box has been offline on the tailnet for 9+ days (§3 below), and
   opening it up is a physical act. Per operational-protocol instinct 3, this is queued as a
   physical task, not a gate: **TODO(Frank): confirm installed RAM on the Topton
   MW-JSL2.5G-4LV10 (`dmidecode -t memory` or the BIOS splash) and correct whichever external
   record is wrong.** It does not block any step in the scratch doc's §2/§3 rewire sequence.

These are corrections to *external* records (FLEET_MAP, claude_memory, lumen_state) that
live outside this repo and outside this worktree's writable paths. Per instinct 7 ("memory
consolidation stays proposal-only"), this doc is the proposal; it does not auto-edit those
files. The two concrete edits for whoever next touches them: fix the TP-Link entry in
claude_memory to `192.168.1.0/24` (or remove it as a duplicate of FLEET_MAP's), and resolve
the RAM figure once read off the hardware.

## 2. HA-monitor disarm gate (pre-rewire gate, scratch doc §2.1)

No disarm/re-arm runbook existed in this repo before this ticket (confirmed:
`grep -rln wadjet-ha-monitor.timer research/ ops/` turns up no procedure doc, only consumers
of the monitor's state). The mechanism, read from the live scripts at `/home/lumen/bin/`:

- The monitor (`wadjet-ha-monitor.sh:22`) gates auto-promote on the presence of a flag file,
  `$HA_DIR/AUTO_PROMOTE_ENABLED` (`HA_DIR=/home/lumen/ha`, `wadjet-ha-monitor.sh:14`). Absence
  of the file means the monitor still probes and logs but takes no action
  (`wadjet-ha-monitor.sh:212-213`: "Ma'at down Nx but AUTO_PROMOTE disabled — alert only").
  `wadjet-ha-monitor.sh --status` (`:147-150`) prints `auto_promote: ENABLED|DISABLED` by
  testing `[ -e "$ENABLE_FLAG" ]`.
- `wadjet-ha-failback.sh status` (`show_status`, same directory) prints the lag/recovery
  pair the scratch doc's "lag=0" gate means in practice: `Ma'at in_recovery`, the standby's
  `in_recovery`, and both sides' `pg_stat_wal_receiver`/`pg_stat_replication` rows.
- Mutating HA actions in that script already run exclusively via detached `systemd-run`
  (`--unit=... --collect --property=Type=oneshot --setenv=_DETACHED=1`, see the
  `rebuild-maat`/`switchover` dispatch), per instinct 5 (vault path changes only via detached
  systemd-run, never foreground/unattended). The disarm/re-arm steps below follow the same
  convention.

**Disarm (run before any LAN rebuild step in scratch doc §3e/§3f):**

```
ssh lumen@100.117.66.102 'sudo systemd-run --unit=wq4011-ha-disarm --collect \
  --property=Type=oneshot -- systemctl stop wadjet-ha-monitor.timer'
ssh lumen@100.117.66.102 'sudo rm -f /home/lumen/ha/AUTO_PROMOTE_ENABLED'
ssh lumen@100.117.66.102 '/home/lumen/bin/wadjet-ha-monitor.sh --status'   # confirm auto_promote: DISABLED and the timer inactive
```

**Re-arm (only after the LAN rebuild is verified, scratch doc §3g):**

```
ssh lumen@100.117.66.102 '/home/lumen/bin/wadjet-ha-failback.sh status'
# gate: Ma'at in_recovery = f (it is the live primary), standby in_recovery = t,
# standby repl_out state = streaming, no lag reported. Do not re-arm on anything else.
ssh lumen@100.117.66.102 'sudo touch /home/lumen/ha/AUTO_PROMOTE_ENABLED'
ssh lumen@100.117.66.102 'sudo systemd-run --unit=wq4011-ha-rearm --collect \
  --property=Type=oneshot -- systemctl start wadjet-ha-monitor.timer'
ssh lumen@100.117.66.102 '/home/lumen/bin/wadjet-ha-monitor.sh --status'   # confirm auto_promote: ENABLED and the timer active
```

This closes the "pre-rewire gate" item in scratch doc §2.1: the gate now has a documented,
copy-pasteable, SSH-wrapped, detached-systemd-run procedure instead of a prose description
of what disarming "would" involve.

## 3. WAN topology — unresolved physical fact, non-blocking

Where the OPNsense WAN port physically plugs in (Sonic ONT directly, vs. behind the
friend's TP-Link) is not recorded in FLEET_MAP, claude_memory, scratch/, or lumen_state
(scratch doc §1, §5). `tailscale status` on Lumen still shows `opnsense-fw` `offline, last
seen 9d ago` as of this session, so it cannot be queried live either. Per instinct 3, this
is queued as a physical task, not a gate on the rest of §1/§2 above:

**TODO(Frank): confirm which uplink the OPNsense WAN port is cabled to (Sonic ONT vs.
TP-Link), and power the box back onto the tailnet so `tailscale status` shows it active
before the Flint 3 demotion step (scratch doc §3e) proceeds** — the scratch doc's own gate
(§A1 addendum) already says the same: do not demote the Flint until `opnsense-fw` shows
`active`, not `offline`.
