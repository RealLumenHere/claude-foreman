# wq#1652 — single cloudflared actuator with cooldown

Source finding: `research/FLEET_SERVICE_ATLAS.md` §3.2 (wq#1627). Three
uncoordinated restarters of `cloudflared.service` — the one tunnel every
`*.lumennode.uk` hostname and the Commander MCP session ride:

| restarter | trigger | how it restarts today |
|---|---|---|
| `lumen-self-healer` | watch-table entry (unit/port down), 60 s poll | own judgment, holddown-aware only |
| `lumen-public-guardian` | heal ladder on S1–S4 stream reds, 2 min | `sudo systemd-run --collect systemctl restart cloudflared.service` |
| `wadjet-ha-monitor` (root) | vault failover origin flip, 30 s | `systemctl kill -s HUP`, falls back to `systemctl restart` |

None knows about the others. Two firing close together, or one firing while
Commander is mid-script, is the documented SIGKILL-mid-script outage vector
(atlas §1.3, §4.5). Instinct 5 (detached systemd-run only) protects *how* a
restart runs, not *whether* it should run at all.

## Design: one owner, three requesters

`cfd_actuator.py` (installed as `/home/lumen/bin/cfd-actuator`) is the single
code path allowed to bounce cloudflared.

**Requesters** (the three above, and anything future) file intent instead of
acting:

    /home/lumen/bin/cfd-actuator request --from <caller> \
        --kind restart|reload --reason "<observed evidence>"

That opens an aq row `source=cfd-restart-req, key=<kind>:<caller>` — the
request bus is the alert queue, so every restart request is durable, visible
in triage, and lands in the same escalation machinery as everything else
(wq#1649). If ≥5 rows are already open when a new request arrives, the
*requester* pages urgent: a dead drain timer cannot page for itself.

**The owner** is `lumen-cfd-actuator.timer` → `cfd-actuator drain` every
60 s, which serializes everything:

1. **holddown** (wq#967): `holddown check cloudflared` ACTIVE → a session
   owns the unit; defer all rows. Pages urgent if a hold starves requests
   >30 min.
2. **slock**: acquires scope `cloudflared` (`--ttl 5m`, holder
   `cfd-actuator`) before acting; deny → defer to next tick. Never piped
   (wq#532 — exit status is the safety signal).
3. **cooldown**: at most one **restart** per 300 s (`CFD_COOLDOWN_S`).
   Requests inside the window are deferred, never dropped — served on the
   first drain after expiry. Worst case Commander is interrupted once per
   5 min instead of three ways at once.
4. **coalescing**: N open rows → one restart. `--kind reload` means "config
   changed, apply it now" (wadjet's origin flip): it is served by the same
   restart but skips the cooldown, carrying any cooling restart rows with it.
   There is no graceful reload. cloudflared 2026.7.3 traps only
   SIGTERM/SIGINT (`cmd/cloudflared/tunnel/signal.go:14` at tag 2026.7.3),
   so SIGHUP exits it, and systemd counts SIGHUP as a clean exit
   (`systemd.service(5)` SuccessExitStatus=), so `Restart=on-failure` on
   Lumen's unit leaves the tunnel down (wq#3575). The actuator never sends HUP.
5. **act**: restart via the exact detached form from ISSUE-LOG #46 /
   guardian's `HEAL_ALLOWLIST`, byte-identical to the sudoers rule
   (no `--unit`, sudoers rejects wildcards in arguments):
   `sudo /usr/bin/systemd-run --collect /usr/bin/systemctl restart cloudflared.service`
   then poll `is-active` up to 30 s; 3 consecutive failures page urgent.
6. **resolve**: served rows are marked resolved with the action + caller
   list in `note`, and a default-priority ntfy documents who asked.

State (`/home/lumen/state/cfd-actuator/state.json`): `last_restart_ts`,
`consec_fails`, `pending_since`, `holddown_escalated`. Latency cost of the
bus: one drain tick, ≤60 s — acceptable for all three callers (healer already
waits 3 poll cycles; guardian cadence is 2 min; a failover origin flip is a
reload, served next tick, cooldown-free).

Tests: `tests/test_cfd_actuator.py` (12 cases, zero live services) — request
filing, dead-drainer escalation, coalescing, cooldown defer/serve, reload
restarts through cooldown, no HUP path anywhere, read-only status, holddown
block, slock deny, fail paging.

## Rollout (staged, no flag day; each stage reversible)

Deploy is a separate op session via `lumen-run --root` — the build lane only
lands files. Until stage 4 the old direct paths still work; double-restart
risk during migration is no worse than today.

- **Stage 0** — `sudo bash install.sh`: installs the CLI, sudoers
  (`visudo -cf` checked), timer. Touches nothing about cloudflared itself:
  the smoke is `cfd-actuator status` (read-only), since any filed request
  restarts the tunnel on the next drain tick.
- **Stage 1 (wadjet-ha-monitor.sh)** — replace `reload_cloudflared()` body
  (bin/wadjet-ha-monitor.sh:102-107) with:
  ```bash
  reload_cloudflared(){
    /home/lumen/bin/cfd-actuator request --from wadjet-ha --kind reload \
      --reason "vault failover: CF origin flipped" 2>>"$LOG" \
      && log "reload_cloudflared: requested via cfd-actuator" \
      || log "reload_cloudflared: ERROR filing request"
  }
  ```
  (Runs as root. alerts.db is WAL, and a root first-opener would leave
  root-owned `-wal`/`-shm` files that break every lumen aq writer, so
  `request` re-execs itself as lumen via `runuser` when euid is 0.)
  Same stage: `bin/wadjet-ha-failback.sh` step [3/6] (`kill -s HUP ||
  restart`, :174) files the same `--kind reload` request. Until stage 1
  lands, both scripts' HUP leaves the tunnel down after a vault flip.
- **Stage 2 (self-healer)** — `ops/wq3575-healer-cfd-actuator`: sets the
  cloudflared entry's `restart_cmd` to `/home/lumen/bin/cfd-heal-request`, a
  zero-argument wrapper for
  `cfd-actuator request --from self-healer --kind restart`. A wrapper is needed
  because the healer execs `restart_cmd` as a single argv element, with no shell.
  The healer's own holddown check becomes redundant for this unit (the
  actuator re-checks); its 3-strike ntfy still works because a filed request
  that goes unserved keeps the unit down and the healer re-fires.
- **Stage 3 (public-guardian)** — in `ops/wq1252-lumen-public-guardian/guardian.py`
  `HEAL_ALLOWLIST`, replace the systemd-run cloudflared entry with
  `["/home/lumen/bin/cfd-actuator", "request", "--from", "public-guardian", "--kind", "restart", "--reason", "heal ladder"]`
  and redeploy guardian + drop its cloudflared sudoers line (keep
  pokemon-live). Separate PR: guardian's sudoers and allowlist must move
  together (ISSUE-LOG #46 convention).
- **Stage 4** — remove the remaining direct sudoers grants for cloudflared
  outside `/etc/sudoers.d/lumen-cfd-actuator`. Break-glass restart remains
  Frank/an op session running the detached systemd-run form by hand.

## Same pattern for the three RAM actuators (design; implementation = follow-up ticket)

Atlas §3.2: `lumen-node-killer` (continuous, reaps DC orphans),
`lumen-rosco` (hourly, kills 7-day-old processes when free <4 GB,
**name**-allowlist), healer RAM-crit (90 %) — three killers, no shared
cooldown, and rosco's protected-name list (`node`, `python`, …) does **not**
protect a 7-day-old `bash` wrapper: an fworker or `lumen-run` wrapper is fair
game today. That is the sharpest edge; the allowlist fix cannot wait for the
owner daemon, so it ships in two parts:

1. **Now (with this ticket)**: `ram-protect.list` — cmdline *regex*
   protect-list (not bare names). Rosco's deploy edit sources it in addition
   to its name list; any cmdline matching a line is never killed:
   fworker/lumen-run wrappers, claude harness processes, holddown/slock
   critical sections. One file, one truth, all three killers read it.
2. **Follow-up (`ram-actuator`, mirror of this design)**: single owner
   draining `source=ram-kill-req` rows behind slock scope `ram-actuator`,
   10-min cooldown between kill sweeps, kills only processes matching the
   request AND not matching `ram-protect.list`. node-killer / rosco / healer
   RAM-crit become detect-only requesters. Node-killer's DC-orphan reaping is
   the one latency-sensitive case: it keeps a direct kill path but *only* for
   cmdlines matching its own DC-identity regex — that path can never touch a
   wrapper, so coordination matters only for the generic RAM sweeps.

Requester escalation, cooldown-defers-never-drops, and the
requester-side-pages-when-owner-dead property carry over unchanged.
