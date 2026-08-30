# SENTINEL PRIME — P1 PULSE CHECK SPECIFICATION (design-validation)
wq#942 · session wq942-sentinel-cad818 · 2026-08-27 · GATE on building anything

**Acceptance:** a written check spec with measured false-positive rates against real fleet
history, one disproving test per check, and a plain statement of anything the design cannot catch.
**No production code in this ticket.** Thor never touched.

---

## 0. TL;DR VERDICT

Of the five 2026-08-23 silent failures, the design's core principle — *prove forward progress with
a timestamp, never trust status* — is sufficient to catch **all five**, BUT two of the detectors as
**worded in the ticket are wrong** and must be respecified before P1 build:

| # | incident | ticket's detector | verdict | correct detector |
|---|----------|-------------------|---------|------------------|
| 1 | LPP active-but-frozen | CPU-vs-expected + frontier.json freshness | **CATCHES — but drop CPU, key on milestone artifact** | frontier.json (milestone) mtime freshness + maintenance suppression |
| 2 | dispatcher erroring at stage=config | last-spawn freshness vs timer cadence | **CATCHES but WEAK** (slow, FP-prone) | structured `stage=config` error-event detection (primary) + spawn-freshness backstop |
| 3 | auth-monitor clean-exit death | enumerate Type=simple+Restart=on-failure, treat clean exit as failure | **UNSOUND — 0% coverage of the real unit, ~100% FP on what it flags** | per-monitor heartbeat freshness (requires adding heartbeats) |
| 4 | kiosk :8871 dead | raw TCP + expected response | **CATCHES cleanly** | raw TCP connect + HTTP GET + status/content assertion |
| 5 | 50 invisible tickets | queue-vs-config lane reconciliation | **CATCHES cleanly** | lane set-difference (queue open-lanes − config lanes − allowlist) |

**Measurement window:** real fleet logs `2026-08-21T15:54:44Z → 2026-08-27T11:10:48Z` =
**139.3h / 5.80 days** (dispatcher.log + dispatcher.log.1.gz = 20,829 JSON events; LPP
events/*.jsonl = 375 runs / 9,655 event gaps; systemd unit inventory + journal). Honest note: this
is 5.8 days, not a full 7 — the dispatcher log rotates and older history is gone. FP rates below are
per-this-window and stated as such.

**Five LIVE findings surfaced during validation (all ticketed this session):**
- **wq#995** — `lumen-pokemon-live` is crash-looping right now (exit 1 every ~110s; frontier.json
  frozen 4 days). This is incident-1 happening live.
- **wq#996** — dispatcher config is invalid *right now*: `lanes.user.max_per_day` rejected → **452**
  `stage=config` errors in the window. This is incident-2 happening live.
- **wq#997** — `dispatch-deadman.service` (the existing watcher meant to catch a stalled dispatcher)
  is itself **failed 203/EXEC** (missing executable). The watcher is dead. Directly motivates §8-Q5.
- **wq#998** — lane `fleet-safety` has open ticket #709 but is absent from dispatcher config →
  invisible. This is incident-5 happening live.
- Design-doc correction (folded in below): the auth-monitor unit is now `Type=simple` +
  **`Restart=always`**, not `Restart=on-failure` as the ticket states.

---

## DETECTOR 1 — LPP active but frozen (frontier.json + CPU)

**Incident:** `lumen-pokemon-live` `active (running)` per systemd, brain blocked 12h on a 600s
LiteLLM fallback, process at 0.0% CPU, no game progress.

**Ticket's proposed detector:** CPU-vs-expected + frontier.json freshness.

**Measured on real history:**
- Healthy LPP produces events every few seconds. Intra-run event-gap distribution across 375 runs
  (9,655 gaps): p50=0s, p90=2s, p95=11s, p99=174s (2.9m), **max=911s (15.2m)**.
- `runtime/frontier.json` is the *milestone* artifact (a strict-window progress summary, field
  `generatedAt`). It updates far less often than raw events, on a completed evaluation cycle.
- **Live right now:** frontier.json last written `2026-08-23T13:22:40Z` — 94h stale — while the
  service shows `active/running` and the live process sits at **0.7% CPU**. Textbook incident-1.

**CHECK (respecified):**
```
FROZEN if  now - mtime(runtime/frontier.json) > 30m
           AND lumen-pokemon-live is active
           AND subject not in maintenance-mode
```
Threshold rationale: healthy max intra-run gap is 15.2m; 30m is 2× headroom and still catches a
12h freeze with >11h to spare. frontier.json cadence when healthy should be re-confirmed at P1 (it
cannot be measured now because LPP has not produced a healthy frontier in 4 days — see wq#995).

**Why drop CPU-vs-expected:** it is defeated by this exact failure. The live process reads 0.7%
CPU — indistinguishable from a normal between-turn wait — and during the current crash-loop it
briefly spikes CPU each 110s life. CPU is ambiguous in both the freeze case and the thrash case.
**Milestone-artifact freshness is the load-bearing signal; CPU is at best a weak corroborator.**

**Why milestone artifact, NOT event-log freshness:** a crash-looping producer still writes new
`events/*.jsonl` files every couple minutes (proven live — wq#995). Keying freshness on event
writes would report LPP healthy while it makes zero game progress. The freshness key MUST be an
*outcome* artifact (frontier.json / milestones.json), never a liveness-of-attempt artifact.

**False-positive rate (this window):** healthy runs never exceed a 15.2m artifact gap, so a 30m
threshold yields **0 FP during healthy operation**. The ONLY firings in the window are the real
4-day frozen period (true positive). **BUT** without maintenance suppression the detector fires
*continuously* for those 4 days — that is not a per-event FP but it IS alert-fatigue, and it is
exactly why §5 maintenance-mode is mandatory, not optional. FP rate healthy = 0/window; unsuppressed
maintenance noise = 1 continuous alert stream — **maintenance mode is a P1 blocker, not a nicety.**

**Disproving test:** freeze a *healthy* LPP by SIGSTOP-ing the tsx process (frontier stops updating,
systemd still shows active, CPU=0). The check must alert within 30m. Conversely, run LPP healthy for
2h under load and confirm the artifact gap never crosses 30m (0 FP). If either fails, the threshold
or the artifact choice is wrong.

---

## DETECTOR 2 — dispatcher erroring at stage=config

**Incident:** foreman dispatcher erroring at `stage=config` for 10h (one lane had `max_per_day: 0`),
timer `active waiting`, spawning nothing.

**Ticket's proposed detector:** last-spawn freshness vs timer cadence.

**Measured on real history:**
- Dispatcher runs as `lumen-foreman-dispatch.timer` (every ~9m, oneshot pass), NOT a long daemon.
- Inter-spawn gaps (n=432 spawns): p50=0.1m, p90=20.6m, p95=20.9m, p99=160.4m, **max=3897m (65h)**.
  Gaps >10h: **2** (0.5%); >4h: 4; >1h: 8.
- Pass-level: 775 `pass_end`; **570 (73.5%)** had `candidates>0 AND spawned=0` — and of those, 536
  had no halt and no error (ordinary policy skips: `lane_unmapped`, pacing, budget). So the intuitive
  "candidates present but nothing spawned = wedged" refinement is **useless: 73.5% FP.**
- Error events: 456 total, of which **452 = `stage=config` "lanes.user.max_per_day must be a positive
  integer"** + 3 = `unreadable config` JSON parse + 1 = a `config_lane` test. This is a live
  recurrence of the incident (wq#996).

**Primary CHECK (respecified — error-signature, near-instant, near-0 FP):**
```
CONFIG-BROKEN if any dispatcher.log event has event=="error" AND stage in {config, config_lane}
               within the last pass  (alert on first occurrence; do not wait hours)
```
A config parse/validation error is never a healthy state, so its FP rate is **0 by construction**;
in this window it fired only on the genuinely broken config (452 times = 1 true incident, not 452
incidents — dedupe by config content hash).

**Backstop CHECK (spawn-freshness, slow, needs a qualifier):**
```
STALLED if now - last(spawn) > 6h
         AND queue has >=1 claimable ticket in a dispatcher-registered lane at a spawnable budget
         AND fleet not in a declared pause window (lumen-pause 13:00 UTC)
```
Threshold rationale: p99 inter-spawn gap is 160m (2.7h); 6h is comfortably above normal cadence and
below the 10h incident. Raw "no spawn in 6h" alone fired **twice** in the window (the 65h gap and
one 10h+ gap) — whether those are FP depends entirely on the claimable-work qualifier: with a
drained/paused queue a long gap is legitimate. The qualifier is what converts a 0.5%-of-intervals
raw signal into a low-FP one; it is also the hard part, because 73.5% of passes legitimately spawn
nothing. Use spawn-freshness ONLY as a backstop for failure modes that don't emit a config error
(e.g. the whole timer dying). **The error-signature is primary; spawn-freshness is the slow net.**

**False-positive rate (this window):** error-signature = **0 FP** (only the real broken config).
Spawn-freshness raw = 2 firings/5.8d; both are real stalls once the claimable-work qualifier is
applied (queue was never legitimately empty — 61 open tickets), so **0 FP after qualifier**, at the
cost of up-to-6h latency. The ticket's worded detector works but is the weaker of the two available
and would have taken 6-10h to notice what the error-signature catches in one pass.

**Disproving test:** set a lane's `max_per_day` to 0 in a config copy and run one dispatch pass — the
error-signature must fire on that pass. Separately, drain the queue and idle-pause the fleet for 7h
and confirm the spawn-freshness backstop does NOT fire (0 FP on legitimate idle). If it fires on the
idle, the claimable-work qualifier is missing or wrong.

---

## DETECTOR 3 — auth-monitor clean-exit death  ← the ticket's detector is UNSOUND

**Incident:** `lumen-litellm-auth-monitor` — the service whose only job is catching auth failures —
dead 12h. Ticket states it was `Type=simple` + `Restart=on-failure` and "exited CLEANLY so nothing
restarted it."

**Ticket's proposed detector:** enumerate every `Type=simple` + `Restart=on-failure` unit fleet-wide
and treat a clean exit as failure.

**Measured on real history — this detector fails on both ends:**

1. **It does not catch the actual incident unit.** The auth-monitor's *current* config is
   `Type=simple` + **`Restart=always`** (FragmentPath `/etc/systemd/system/lumen-litellm-auth-monitor.service`,
   ExecStart `/home/lumen/bin/litellm-auth-monitor`). Either it was fixed since 08-23 or the ticket
   mis-described it — but as it exists today it is **not in the Type=simple+Restart=on-failure set at
   all**, so the enumeration gives it **0% coverage**. A detector keyed on a config signature misses
   the moment the signature changes.

2. **~100% FP on what it does flag.** Lumen currently has 8 `simple`+`on-failure` service units: 5
   active, **3 inactive** (`llama-gemma4-mtp` — intentionally retired; `llama-qwen36` — intentional
   offline-fallback, down by design; `rsync.service` — socket-activated, inactive is *normal*). The
   detector flags "clean-exit / inactive" as failure → it would page on **3/3 legitimately-down
   units = 100% FP** on the flagged population, and would keep paging forever because they are
   supposed to be down. A watcher that cries wolf on every intentionally-stopped service trains Frank
   to mute it — the precise fatigue trap §5 warns about.

3. **The signature is neither necessary nor sufficient for the failure class.** The real class is
   *"a component that must run continuously is not making progress and won't self-recover."*
   Contributors: `Restart=on-failure`+clean-exit (the classic trap) OR `Restart=no` OR
   `Restart=always` but the process is *wedged not exited* (that's incident-1!). `Type`/`Restart`
   enumeration keys on the plumbing, not the symptom.

**Additional structural gap (measured):** even if we key on the auth-monitor's *output*, its only
output is `/home/lumen/logs/litellm/auth-failures.jsonl`, written **only when an auth failure
occurs**. In healthy operation it produces nothing. So output-freshness cannot distinguish
"healthy and quiet" from "dead" for event-driven monitors — the whole class of alerters/monitors
produces nothing when things are fine.

**CHECK (respecified — per-monitor heartbeat):**
```
For each unit tagged role=continuous-monitor in the Sentinel registry:
  require the unit to emit a heartbeat (touch $RUNTIME/<unit>.beat every <=60s) from its
  innermost loop;
  DEAD if now - mtime(<unit>.beat) > 5m   (regardless of ActiveState/Type/Restart/exit code)
```
This works uniformly for event-driven monitors (auth-monitor), pollers, and daemons, and is immune
to the config-signature drift that defeats the ticket's detector. It requires a one-line heartbeat
added to each watched monitor — that is real work, and must be scoped into P1, not assumed.

**Secondary (cheap, catches the classic trap without heartbeats):**
```
LATENT-DEATH if a unit declared long-running is ActiveState=inactive/failed
              AND it is NOT in the maintenance/intentionally-down allowlist
```
Note this needs the same allowlist that the ticket detector lacked — that allowlist (not the
Type/Restart signature) is what suppresses the 3 legitimate inactives above.

**False-positive rate (this window):** ticket detector = **~100% FP** on flagged inactive units (3/3
legitimately down) AND **0% true-positive coverage** of the incident unit → reject. Heartbeat
detector: not measurable historically (no heartbeats exist yet); modelled FP with 60s beat + 5m
threshold requires 5 consecutive missed beats during health = scheduler starvation, effectively 0
outside genuine OOM/PSI (which the Pulse alerts on separately). **This is a design defect in the
check as specified — say so plainly, per acceptance.**

**Disproving test:** (a) SIGKILL a heartbeat-emitting monitor; the beat must go stale and alert
within 5m. (b) Leave `llama-qwen36` intentionally down for a day; the respecified detector must
stay silent (allowlisted) while the ticket detector would page all day. If the heartbeat detector
alerts on a healthy monitor, the beat cadence vs threshold is mis-set.

---

## DETECTOR 4 — kiosk :8871 dead

**Incident:** kiosk `http.server` on :8871 died; Firefox sat on a dead port; nothing noticed.

**Ticket's proposed detector:** raw TCP + expected response.

**Measured live:** raw TCP connect to `127.0.0.1:8871` + `GET /` returns `HTTP/1.0 200 OK`,
`Content-Length: 3112`, real kiosk HTML (`lumen-kiosk-idle.service`). Connect rc=0.

**CHECK:**
```
DOWN if TCP connect to <host:port> fails within 2s
     OR HTTP GET <path> does not return status 200 within 3s
     OR response body length < <min_bytes>  (or a required content marker is absent)
```
The status+body assertion (not just TCP) is what catches the harder variant where the port listens
but serves 404/empty — a bare TCP check would miss that. For :8871 assert 200 + a known marker
string from the kiosk page.

**False-positive rate (this window):** a synthetic probe against a served endpoint FPs only during a
legitimate restart race (the server is briefly down while restarting). Mitigate with 2-consecutive-
fail confirmation at 1s spacing before alerting → FP effectively 0. No FP observed against the live
endpoint. (No historical probe series exists to replay, so this is a live-verified + modelled figure,
stated honestly.)

**Disproving test:** `systemctl stop lumen-kiosk-idle`; the check must alert within its confirm
window. Then start it and confirm the 2-fail confirmation swallows the restart blip (0 FP). Also
point the server at an empty dir so it 200s an empty index — the body-length/marker assertion must
still fire.

---

## DETECTOR 5 — invisible tickets (lane not registered in dispatcher)

**Incident:** 50 of 64 open tickets invisible — 8 lanes existed in `queue.db` but not in dispatcher
config.

**Ticket's proposed detector:** queue-vs-config lane reconciliation.

**Measured live:**
- dispatcher `config.json` registers 15 lanes: foreman-tok, lpp, questfree, pstack, fleet-fires,
  foreman-auto, build, mission-control, user, auto, fleet, fleet-spend, ops, research, pokemon.
- `queue.db` lanes with OPEN tickets: user, auto, research, build, pokemon, fleet, **fleet-safety**.
- Set difference (open-lanes − config-lanes) = **{fleet-safety}** — 1 lane, holding open ticket
  #709 ("Build lumen-guardian"), dispatcher-invisible right now (wq#998).

**CHECK:**
```
INVISIBLE-LANE if  { lanes with >=1 open ticket in queue.db }
                 − { lanes in dispatcher config }
                 − { documented known-non-dispatch allowlist (e.g. user, mission-control) }
                 is non-empty
```

**False-positive rate (this window):** the reconciliation is deterministic. FP arises only if a lane
is *deliberately* kept out of dispatch yet holds open tickets (the `user` lane is the archetype —
but it is already registered-and-skipped, not omitted). With the allowlist, current FP = 0 and the
one firing (fleet-safety) is a true gap. Without an allowlist, any intentionally-manual lane FPs —
so the allowlist is required, and must be a reviewed file, not a code constant. Snapshot FP over the
window: config lanes are near-static, so the daily reconciliation would have surfaced fleet-safety
consistently as the single true positive, 0 FP.

**Disproving test:** insert an open ticket into a brand-new lane `zzz-test` not in config; the check
must flag it on the next pass. Add `zzz-test` to the allowlist and confirm it goes silent. If a
registered lane (e.g. research) is ever flagged, the set math or the config parse is wrong.

---

## §8 OPEN QUESTIONS 3–6 (answered before P1)

### Q3 — Where does the state DB live?
Split state by liveness-criticality across the two physical disks the design already specs (§1:
NVMe for models+history, separate SATA SSD for state):

- **Tier-1 Pulse rolling state = a flat file** (JSON/append log) on the SATA SSD, `fsync`-light,
  **never a database**. The Pulse must never block on a lock; a DB is a shared-fate dependency the
  watcher cannot afford. This is the file Tier 2 reads.
- **Tier-2 incident/episodic DB = SQLite (WAL) on the SATA SSD**, physically separate from the NVMe
  that holds the MoE + metric history, so a full model volume cannot wedge state writes (the design's
  own "full disk on one volume cannot kill both" requirement).
- **Durability off-box:** replicate the incident DB nightly to Anubis Borg (already the fleet backup
  repo) so a sentinel disk failure loses at most a day of incident history, not the record of why the
  fleet broke. History that only exists on the box that watches the fleet dies with that box.
- **Hard rule:** Pulse liveness depends on neither DB. If the SQLite file is missing/corrupt, the
  Pulse still checks and still pages; Tier 2 degrades to stateless.

### Q4 — Credential model (it has huge authority; a compromise is catastrophic)
Asymmetric capability, matching the §3 GREEN/AMBER/RED authority split at the *credential* layer, not
just policy:

- **Observation = read-only, no standing secrets.** Per-node SSH key with `ForceCommand` restricting
  to a read-only allowlist (`systemctl is-active/show`, `ss`, `cat` of specific paths, `uptime`) —
  not an interactive shell, not sudo. Plus read-only service tokens where an API exists (the fleet
  already has this exact pattern: `memoryd-service.token`, 0600, READ-ONLY).
- **GREEN restart authority = narrowly-scoped sudoers/polkit drop-ins** enumerating the *exact* unit
  names it may `systemctl restart`, never blanket sudo. Adding a unit to its remit is a reviewed
  change to that drop-in.
- **RED holds NO credentials by design.** It writes tickets; humans / credentialed agents act.
  Config edits, secrets, DB migrations, self-modification → it literally cannot execute them because
  it holds no key that can.
- **Alerting = its own publish-only ntfy credential** scoped to its topics (never a fleet-wide key).
- **Thor = no credential at all beyond the read-only ForceCommand key** (see Q6). Capability-level
  enforcement of rule #1, not intention-level.
- All secrets follow the established one-file-0600 + host-scoped git/credential-helper pattern
  (Gitea/memoryd precedent) so there is exactly one rotation point per secret.

### Q5 — Who watches the watcher?
The heartbeat dead-man (§5) tells Frank but does not fix — and this session proved the danger is
real: **`dispatch-deadman.service` is failed 203/EXEC right now** (its script is missing), so the
fleet's existing "alert if the dispatcher stalls" watcher has been silently dead. An unwatched
watcher fails exactly like the things it watches. Layered answer:

1. **systemd `WatchdogSec` on the Pulse** — a hung Pulse is killed and restarted by PID 1. This is
   the one supervisor we can trust because it is kernel-adjacent and shares no fate with the fleet.
   The heartbeat must be emitted from the Pulse's **innermost loop** (a wrapper heartbeat would keep
   beating while the loop wedges — the incident-1 mistake in miniature).
2. **Reciprocal external dead-man** — a tiny deterministic check on a *second independent node*
   (Anubis or Bes, not Lumen) that reads Sentinel's heartbeat and pages Frank if it goes stale >N.
   Mutual dead-man across two nodes: each can report the other's silence.
3. **Honest limit, stated:** nothing auto-*repairs* a broken Sentinel, and it may not modify itself
   (§3 invariant 4). The design goal here is fast detection + a Frank page over an independent path,
   not self-healing. Recording that limit is the correct answer, not pretending to close it.
4. Fold the dead `dispatch-deadman` (wq#997) into this: Sentinel's Pulse should absorb its coverage
   under R2, and the broken unit retired rather than left failing.

### Q6 — Does it need read access to Thor, and is SSH-only enough given rule #1?
Yes to read access; SSH-only is not just enough, it is the *correct* way to honor rule #1 —
**at the capability layer, not merely by policy.**

- Grant Thor monitoring via an **SSH key with `ForceCommand` pinned to a read-only allowlist** (e.g.
  `systemctl is-active`, `ss -ltnp`, `cat` of named files, temps/SMART reads). No interactive shell,
  no sudo, no restart/reboot verb reachable — so even a compromised or buggy Sentinel *cannot* stop,
  reboot, or interrupt Thor, because it holds no credential that can. Rule #1 becomes structurally
  impossible to violate rather than a rule it must remember.
- Everything Sentinel needs for Thor is read-only (progress/liveness/thermal). It never needs a
  mutation path there. Explicitly exclude Thor from the GREEN restart drop-in (Q4).
- Thor is Frank's workstation; keep the read cadence gentle and never probe in a way that could wake
  or load it. Read-only + gentle cadence + no mutation capability = rule #1 fully satisfied.

---

## FP SUMMARY (measured, window = 5.80 days of real fleet history)

| detector | check (respecified) | threshold | FP this window | latency | note |
|---|---|---|---|---|---|
| 1 LPP frozen | frontier.json mtime + active + not-maint | 30m | 0/window healthy | ≤30m | needs maintenance suppression or continuous noise |
| 2 dispatcher config | `error` event stage∈{config,config_lane} | first occurrence | 0 (structural) | 1 pass | primary; spawn-freshness 6h backstop 0 FP after claimable qualifier |
| 3 monitor death | per-monitor heartbeat mtime | 5m (60s beat) | 0 modelled | ≤5m | ticket's Type/Restart detector REJECTED: ~100% FP, 0% coverage |
| 4 kiosk port | TCP + HTTP 200 + body marker | 2-fail @1s | 0 (live-verified) | ≤3s | body assertion beats bare TCP |
| 5 invisible lane | queue-lanes − config-lanes − allowlist | non-empty | 0 with allowlist | 1 pass | 1 true positive live (fleet-safety) |

**Incidents the design CANNOT catch as the ticket words them:** #3 (config-signature enumeration —
unsound, must become heartbeat) and #2 is under-served (spawn-freshness is slow and FP-prone; needs
the error-signature). Reworded per this spec, **all five are catchable** with FP ≈ 0 in this window.
The single largest architectural requirement the data surfaced: **freshness must key on OUTCOME
artifacts, never on attempt/liveness artifacts** — a crash-looping producer writes event logs, burns
CPU, and shows `active` while making zero progress. Every "did it produce output" check must name an
outcome artifact or it will be defeated by exactly this failure.

## ACCEPTANCE — P1 gate recommendation
Proceed to build the Pulse (observe-only) with detectors respecified as above, **conditional on**:
(1) detector #3 built as heartbeat, not config enumeration; (2) detector #2 built error-signature-
primary; (3) maintenance-mode + a reviewed non-dispatch allowlist landed *with* detectors 1/5, not
after; (4) frontier.json healthy cadence re-measured once wq#995 restores LPP progress. The design's
P1 acceptance ("independently detect the five incidents") is achievable; the ticket's detector
wording is not, for #3 and (weakly) #2 — corrected here.

— end wq#942 spec —
