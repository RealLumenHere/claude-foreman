# wq#2104 — subscribe-side proof for the alert path (design, with verdict)

**Status:** design decided — DO NOT build the ticket's proposed lumen-heartbeat
subscribe path. The mechanism that closes this gap already exists in this repo
(wq#1704's `roundtrip` watchdog) and is strictly stronger; it has simply never
been deployed. The remaining work is a root deploy step, not subscriber code.

**Date:** 2026-09-20 · **Lane:** wq2104-a1 · **Split of:** wq#1650

## 1. Problem (confirmed root cause, unchanged)

`/home/lumen/bin/alert-deadman-beat.sh` (5-min timer, wq#1039) publishes ntfy
topic `lumen-heartbeat` at min priority (`alert-deadman-beat.sh:11,18`) purely
to prove the *publish* path before beating the off-fleet dead man's switch.
Nothing subscribes to that topic: `ntfy_alert_subscriber.py` watches
`lumen-alerts,lumen-updates` only (confirmed independently of the unreadable
source via `systemctl show lumen-alert-queue.service` Description — see
ISSUE-LOG #298 / `ops/wq1704-missing-watchdogs/fleet_spof_watch.py:10-14`).
So a wedged-but-`active` `lumen-alert-queue` is invisible: every alert becomes
phone-only, `aq` triage goes blind, and the beat keeps reporting healthy.

## 2. What changed since the wq#1650 split: wq#1704 built the fix

`ops/wq1704-missing-watchdogs/` (landed on main) ships exactly the
subscribe-side proof this ticket asks for, as the `roundtrip` subcommand of
`fleet_spof_watch.py`:

- Publishes a unique min-priority marker to `lumen-updates` — a topic the
  subscriber *already* watches (`fleet_spof_watch.py:86,232-238`).
- Polls `alerts.db` for a row carrying the marker, deadline 120 s
  (`fleet_spof_watch.py:250-265`).
- On miss: raises ONE deduped aq finding (`source=spof-watch`,
  `key=roundtrip:lumen-alert-queue`) at urgent priority plus an ntfy page
  (`fleet_spof_watch.py:267-275`).
- On success: resolves its own synthetic row so probe traffic never pollutes
  `aq list open`, and edge-clears any open finding
  (`fleet_spof_watch.py:277-284`) — the exact "no perpetual open row" concern
  this ticket's step 2 raises, already solved without a new table.
- Cadence: `lumen-spof-roundtrip.timer`, `OnUnitActiveSec=15m`
  (`lumen-spof-roundtrip.timer:6`).

## 3. Live finding (2026-09-20): built, never deployed

All four wq#1704 timers are `LoadState=not-found` on the box:

    systemctl show lumen-spof-{roundtrip,daemons,timers,restarts}.timer
      --property=Id,LoadState,ActiveState   → LoadState=not-found (all four)

`install.sh` is explicitly a root deploy step "NOT run by the build lane"
(`ops/wq1704-missing-watchdogs/install.sh:3`), and that step never happened.
**The production gap this ticket describes is therefore still open today**, but
the fix is a deployment, not a build.

## 4. Verdict on the ticket's proposed design: reject as redundant

The proposal (add `lumen-heartbeat` to the subscriber's TOPICS; upsert a
`heartbeat_last_seen` row; new 2-min probe alarming at 7 min) is rejected:

1. **No independent signal.** One process subscribes to all its topics. If it
   is wedged, the `lumen-updates` roundtrip already fails; if it is healthy, a
   third topic proves nothing more. The roundtrip additionally proves
   *persistence into alerts.db*, which a TOPICS-side last-seen stamp alone
   would not.
2. **Wrong coupling.** `lumen-heartbeat` exists to gate the off-fleet deadman
   beat on publish-path health (`alert-deadman-beat.sh:16-22`). Overloading it
   as a subscribe-side proof couples two deliberately separate failure
   domains (off-fleet paging vs on-fleet triage).
3. **Higher cost, same coverage.** It needs new schema in `alerts.db` (or
   overloading `group_state`), edits to the shared subscriber in
   `/home/lumen/agents/alert-queue/` — outside worker-lane grants (ISSUE-LOG
   #298), so build *and* test would fight the sandbox — plus a new probe +
   timer that duplicate `cmd_roundtrip` and its edge-clear logic.
4. **Detection latency is close enough.** Roundtrip worst case ≈ 15 min
   cadence + 120 s deadline ≈ 17 min vs the proposed 7 min. The 7-min figure
   was proposed before wq#1704 existed and traces to no stated requirement;
   total-fleet-death is covered separately and faster by the off-fleet deadman.
   If a tighter bound is ever wanted, it is a one-line timer drop-in
   (`OnUnitActiveSec=5m`), not a new subsystem.

## 5. Plan of record

1. **Deploy wq#1704** — run `ops/wq1704-missing-watchdogs/install.sh` as root
   via `lumen-run --root` (it self-gates: pytest suite, `verify_fires.py`
   fire-on-simulated-failure, `smoke.py` against a throwaway alerts.db, then
   installs `/home/lumen/bin/fleet-spof-watch` + 4 unit pairs and enables the
   timers). Filed as a follow-up ops ticket (see done-notes).
2. **End-to-end wedge test (ticket step 4), post-deploy:**
   `systemctl kill --signal=SIGSTOP lumen-alert-queue.service` → expect the
   urgent `roundtrip:lumen-alert-queue` finding within ≤17 min → `SIGCONT` →
   expect edge-clear on the next pass. Belongs to the deploy ticket (needs
   root; a build lane may not wedge the live alert path —
   `verify_fires.py:6-10` records the same constraint).
3. **Deploy-ticket checklist extras:** confirm the wq#1658 universal
   timer dead-man covers `lumen-spof-roundtrip.timer` itself (the probe's
   own TIMERS list deliberately excludes it, `fleet_spof_watch.py:67-82`);
   if not, add it there.
4. **Still open, out of scope here:** subscribe-side proof for
   `lumen-command-receiver` — needs a read grant on
   `/home/lumen/agents/command-receiver/` or a receiver-side no-op verb;
   already recorded as the remainder of atlas §5.1 item 2 in
   `ops/wq1704-missing-watchdogs/README.md` ("Known gaps left open").

No change to `ntfy_alert_subscriber.py`, no new table, no new probe script.
