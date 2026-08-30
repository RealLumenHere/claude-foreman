# R2 — Watchdog Consolidation: Coverage Matrix, Noise, Consolidated Design
Session r2-watchdog-2fdc9f · 2026-08-26 ~13:00 PDT · every cell below was PROVEN by reading
the watcher's code or its live output, not inferred from unit names. Evidence pointers inline.

## 0. TL;DR
- 26 watcher-class units enumerated; **6 different things watch LiteLLM, 0 things watched the
  kiosk server, and 0 things can tell a deliberate hold-down from an outage.**
- The fleet's problem is no longer detection — it is RESPONSE. probe-pokemon-liveness correctly
  paged 152 times over 3 days while LPP sat dead; nobody (human or machine) owns answering an
  alert. Detection without a responder is a dashboard, not a watchdog.
- FIXED LIVE THIS SESSION: (a) lumen-pokemon-live restarted via lpp-restart + re-enabled —
  it was stopped 08-23 by the wq#863 worker whose lease expired with NO paired restore
  (instinct violation, root cause of the 3-day LPP outage); rung qwen36-local verified healthy
  first (completion "OK" via LiteLLM). (b) duplicate wq-notify watch unit (…505) stopped,
  one (…542) kept. (c) dispatch-deadman: already retired (unit+script gone) — O2 stale.
- STILL LIVE, ticketed below: 401 bursts every 30 min at :19/:49 from a host process with a
  stale/absent key (since 08-24 07:50Z, 336 events today alone, caller unattributed);
  dispatcher alternating healthy/unhealthy (spawned=0, candidates=18) with 69 STALLED alerts.

## 1. THE WATCHER INVENTORY (proven roles)
| # | watcher | cadence | mechanism (from code) | acts or alerts? |
|---|---|---|---|---|
| W1 | lumen-self-healer | 60s daemon | systemctl/docker/port checks on FIXED list (redis, prompt-engine, messenger, vip-api/watcher, cloudflared, memoryd, mcp-gateway, desktop-commander-mcp; docker ntfy+litellm) + wq worker-check (new blocked / orphaned claims) | RESTARTS + ntfy |
| W2 | lumen-smart-watchdog | 5m daemon | agent research-stall + RAM%; liveness restarts ceded to W1 | restarts lumen-agent on stall only |
| W3 | lumen-watchdog.timer | 60s | ping 11 device IPs + 5 local ports + local ollama; edge-triggered | ntfy lumen-updates |
| W4 | lumen-model-watchdog | 10m | generate-probe every LiteLLM rung; tags-vs-generate saturation logic (wq#898); pushes Gatus | ntfy + Gatus push |
| W5 | lumen-brain-health | 5m | backend-aware messenger brain probe (claude CLI or LiteLLM) + DeepSeek balance | ntfy urgent |
| W6 | lumen-foreman-health | 5m | dispatch.log tail; 3 consecutive stalled passes → alarm; 1h dedup | ntfy urgent |
| W7 | lumen-foreman-selfheal | 10m | releases INFRA_CRASH wq blocks; ALSO calls dispatcher_health per pass | wq release + ntfy |
| W8 | sentinel-probes | 30m | 14 function-correctness probes incl. pokemon-liveness, litellm-auth-monitor liveness, storage, state-drift, synthetic LLM; FAIL→aq, CRITICAL→ntfy; edge-clears own aq rows | aq + ntfy + Gatus |
| W9 | lumen-sentinel-watch | 5m | curl Gatus :8418; "fleet is unmonitored" page | ntfy urgent |
| W10 | Gatus | 30s-10m | 54 endpoints: WAN, node icmp, local services, model rungs (pushed), tunnels (STATUS<500 + cert), sshauth | ntfy |
| W11 | maat-watch | 5m | Vaultwarden /alive state-change probe | ntfy |
| W12 | wadjet-ha-monitor | 30s | vault DB failover automation | PROMOTES + ntfy |
| W13 | a5-health | 30m | agent5 trio active + dead-rate + review backlog | ntfy |
| W14 | litellm-auth-monitor | daemon | tails litellm container logs → auth-failures.jsonl (LOGS ONLY; burst alerting is downstream aq 'litellm-auth' rows) | log (+aq burst) |
| W15 | lumen-sqlite-health | weekly | ro health across 11 stores | ntfy |
| W16 | repo-visibility-sentinel / git-guard-sentinel / lumen-repo-sweep | 15m/1h/4h | public-repo allowlist / guard bypass / managed-repo divergence (wq#800) | aq / ntfy |
| W17 | lumen-ecc-watch + lumen-ecc-rollup | 5m + daily | EDAC CE/UE delta per event AND a daily rollup (both) | ntfy |
| W18 | mdmonitor, n8n-watcher, lumen-rerank-thread-watch (48h), lumen-split-watch, lumen-ui-drift, dispatch-lint, fleet-auth-ssh-probe, gatus-sshauth-probe | various | narrow single-purpose | ntfy/aq |
| — | lumen-monitor (STATUS.json), wq-notify watch, lumen-vip-watcher | — | snapshots/loggers, NOT watchdogs (references stale units lumen-orchestrator/builder — R3 candidate) | none |

## 2. COVERAGE MATRIX — this week's ACTUAL failures × watchers
Legend: ✔ = detects (proven), ✔* = detects NOW but shipped AFTER the incident (was blind at
incident time), ✘ = provably blind, ⚠ = detects but response never happened.

| Failure (this week) | W1 healer | W3 watchdog | W4 model-wd | W6 fore-health | W7 selfheal | W8 sentinel | W10 Gatus | W14 auth-mon | W16 repo | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Dispatcher config halt 10h (08-22, wq#824/#825) | ✘ | ✘ | ✘ | ✔* (built during/after; 3-stall alarm) | ✔* (embedded health call) | ✘ | ✘ | ✘ | ✘ | covered NOW by 2 overlapping checks; was blind |
| LPP frozen-but-active 12h (08-23, wq#908) | ✘ (unit not in list) | ✘ | ✘ — actively MISLEADING: read qwen36 generate count as health (the generates were LPP's blocked retries) | ✘ | ✘ | ✔* probe-pokemon-liveness — PROVEN: caught the current 3-day outage, 152 FAILs | ✔* (via sentinel push) | ✘ | ✘ | ⚠ detect-yes / respond-NO. 152 pages, zero action until this session |
| Kiosk HTTP server (stream.ts) died | ✘ | ✘ (gameboy NOT in DEVICES — known-gap comment in code) | ✘ | ✘ | ✘ | ✘ (no probe) | ✘ (no endpoint) | ✘ | ✘ | **GAP — Frank is the detector** |
| litellm-auth-monitor dead 12h (wq#897) | ✘ (unit not in list) | ✘ | ✘ | ✘ | ✘ | ✔* probe-litellm-auth-monitor (ActiveState each cycle) + Restart=always | ✔* | n/a (it was the casualty) | ✘ | covered now; was blind |
| LiteLLM 401 storm (wq#896/#914) | ✘ | ✘ (port up ≠ auth sane) | ✘ (probes with valid key) | ✘ | ✘ | ✘ (synthetic uses valid key) | ✘ (liveliness only) | ✔ logs; aq 'burst-401' rows alert | ✘ | ⚠ burst STILL FIRING every 30m at :19/:49 since 08-24; 171 aq repeats, 0 response, caller unattributed |
| Crossed cloudflared hostnames (chat→ttyd, 08-22) | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ — PROVEN: condition is [STATUS]<500 only; ttyd answers 200 and PASSES. Config unchanged today. | ✘ | ✘ | **GAP — still open. Frank was the detector** |
| Repo divergence | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔* lumen-repo-sweep (wq#800, 4h) | covered now |
| 50 invisible tickets | partial — worker-check alerts NEW blocked/orphaned only, silently baselines backlog | ✘ | ✘ | partial — stall implies non-dispatch but not WHY | ✘ | probe-queue-health watches the OLD file queue, not wq | ✘ | ✘ | ✘ | **GAP — nothing watches "created but never dispatched/visible"** |
| (bonus, brief row) Isis single-slot saturation | ✘ | ✘ | ✔ wq#898 tags-vs-generate carry-forward (built after the 9h false-page storm 06:28-15:02Z) | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | covered, with a documented false-positive history |

**Pattern across all 9 rows:** 7 of 9 were blind at incident time; every fix is a NEW watcher or
probe added AFTER its incident (wq#825, #908, #897, #898, #800…). That is the O2 proliferation
engine itself: incident → bespoke watcher → never reconciled. And in the 2 rows where detection
worked (LPP, 401s), nothing answered. The marginal value of another detector is ~zero; the
missing component is a RESPONDER.

## 3. OVERLAPS (same failure watched 3+ times)
- **LiteLLM watched 6×**: W1 (docker restart), W3 (port 4000), W5 (/health/liveliness), W8
  probe-synthetic-llm (content), W10 (liveliness), W4 (every rung). Only W1 acts. Recommend:
  W10 poll + W8 content + W1 act; drop W3's port check and W5's duplicate liveliness.
- **Dispatcher health computed twice per 10min**: W6 timer AND W7's embedded
  check_dispatcher_health() call each selfheal pass — two alerters on one condition, 1h-dedup
  each, out of phase. Plus: dispatch_health exits nonzero when alarming → the failed-unit
  sweep pages "Failed unit: lumen-foreman-health" + "Recovered" — **3 alert rows per stall
  event** (measured: 69 STALLED + 68 failed-unit + 68 recovered in 7d).
- **ntfy watched 3×** (W1 docker+port, W3 port, W10 http). **Ma'at/vault 3×** (W11 app, W12 DB,
  W10 icmp) — this one is layered correctly, keep.
- **ECC alerts 2×**: per-CE event alert (46 in 7d at prio 5) AND a daily rollup unit. Rollup
  should be the only voice below a UE/threshold.
- **wq blocked tickets 2×**: W1 alerts new blocks; W7 auto-releases INFRA_CRASH blocks — W1
  routinely pages for blocks W7 resolves minutes later.

## 4. CONFLICTS
1. **Deliberate hold-down is invisible to every watcher.** The LPP freeze-flag/lpp-restart
   system exists precisely so restarts are licensed — but NO watcher reads the flag or slocks.
   Consequence A (happened): service deliberately stopped → probe pages 152× at nobody.
   Consequence B (latent): add any held-down unit to self-healer's list and the healer fights
   the freeze. The stop/restore contract and the watchers are two systems that don't speak.
2. **wq#863's stop had no paired restore** — the exact instinct "any automation that stops a
   service must carry a paired restore" — and nothing enforces it mechanically. lpp-restart
   guards RESTARTS; nothing guards STOPS.
3. **Duplicate drivers, again**: two wq-notify watch transient units double-appending
   notifications.log since ~08-21 (one stopped this session). Same class as night-watch/O2.
4. **probe-state-drift's must-be-active list has drifted**: it requires lumen-agent active, but
   lumen-agent was deliberately retired from the healer 2026-08-21 and is inactive → the probe
   FAILs daily (6 aq repeats, open, last today 18:54). A watcher alerting on policy, not fault.
5. Historical, resolved correctly and worth copying: smart-watchdog vs self-healer restart race
   was fixed by explicit ownership ("liveness restarts owned by self-healer") — that ownership
   move is the template for the whole consolidation.

## 5. NOISE RATES (7 days, aq alerts.db + journals, measured 2026-08-26)
"actionable" = a human or automation did something because of it.
| watcher/source | alerts (rows×repeats) | actioned | est. actionable % |
|---|---|---|---|
| jay-watch | 103 rows, 0 ack/0 res | 0 | ~0% — activity FEED on an alert queue |
| repo-visibility-sentinel | ~10 rows, repeats in the HUNDREDS (questfree 384+276+171+99+98+60, freemiko 52) | auto-resolved churn | ~0% — allowlist gap re-firing |
| litellm-auth (burst-401) | 171 repeats + 35 ntfy rows | 0 | 100% actionable in principle — TRUE POSITIVE nobody actioned |
| probe-pokemon-liveness | 152 repeats | 0 until this session | true positive, unanswered 3 days |
| DISPATCHER STALLED (W6/W7) | 69 + 68 failed-unit + 68 recovered | 0 visible | real condition, tripled volume |
| model-watchdog "Homelab: models" | 119 repeats | 0 | low — post-wq#898 still chatty |
| "System map updated" (lumen-alerts) | 120 repeats | 0 | 0% — info spam on the PAGE topic |
| ECC per-CE | 46 repeats prio-5 | 0 | ~0% below UE threshold (rollup exists) |
| agent5 split (a5/split-watch) | 26 | ? | low |
| maat-watch / wadjet-ha | 0 pages (vault healthy) | — | quiet when healthy = correct |
| lumen-watchdog | ~0 pages 7d | — | quiet; also ~fully redundant with Gatus |
**Bottom line: >1,500 raw alert events in 7d; the 2 loudest TRUE positives (LPP, 401s) got zero
response. aq open backlog = 211. Frank is being trained to ignore the queue — O4 confirmed.**

## 6. CONSOLIDATED DESIGN — fewest watchers covering every observed failure
Principle: **one poller, one prober, one actuator, one queue-tender, one page path, and a
RESPONDER.** Ownership per failure class, not per incident.
- **POLLER = Gatus (W10).** All up/down: node icmp (ADD gameboy), ports, tunnels WITH content
  assertions per hostname ([BODY]/header match — kills the crossed-hostname class), certs,
  sshauth, kiosk stream server endpoint (ADD). Retire lumen-watchdog (W3) after its device
  list is backfilled into Gatus — it is a second Gatus with a smaller list.
- **PROBER = sentinel-probes (W8).** All function-correctness + product-liveness (LPP probe
  stays; add per-product liveness rows per fleet-audit.md table: messenger round-trip,
  mission-control freshness, glance freshness). FOLD IN: maat-watch (as a probe), a5-health,
  brain-health — same shapes, three fewer timers. model-watchdog stays (rung logic is real) but
  reports ONLY via aq/Gatus, not its own ntfy voice.
- **ACTUATOR = self-healer (W1), the ONLY thing that restarts.** Replace its hardcoded list
  with a declarative watch table: unit → {policy: restart|alert-only, holddown_flag: path,
  owner}. It MUST read hold-down flags (RESTART_FREEZE pattern, generalized) — that single
  change reconciles conflict #1 and would have auto-restored LPP when the wq#863 lease expired.
  Absorb smart-watchdog's stall-restart as one rule; retire the separate daemon.
- **QUEUE-TENDER = foreman-selfheal (W7)** keeps wq work; DELETE its embedded dispatcher_health
  call. **W6 keeps dispatcher health alone**, and exits 0 when it alarms (the alert IS the
  output) — kills the failed-unit echo chamber.
- **PAGE PATH**: exactly one page topic (lumen-alerts) fed only by aq-escalation; info-class
  (System map, jay/github/ebay activity, per-CE ECC) moves to a daily digest topic. Everything
  lands in aq first (mostly true already).
- **RESPONDER (the missing tier)**: an aq escalation rule — any CRITICAL-class key open with
  N≥3 repeats and no ack: (1) auto-seed a P0 wq ticket naming the runbook, (2) one page to
  Frank, (3) stop repeating. Detection→ticket→dispatch closes the loop that left LPP dead 3
  days; today the dispatcher can even work the ticket.
Net: ~26 watcher units → ~8 (Gatus, sentinel, self-healer, foreman-selfheal, foreman-health,
model-watchdog, wadjet-ha-monitor, repo-sweep) + narrow security sentinels. Every 2026-08 row
in §2 stays covered; three classes gain coverage they never had.

## 7. MIGRATION TICKETS (seeded this session, lane=auto unless noted)
- T1 P0 [live defect] 401-burst attribution + fix: half-hourly bursts at :19/:49 from host
  (172.20.0.1) POSTing /v1/chat/completions with bad/absent key since 08-24 07:50Z. Correlate
  the :19/:49 timer/service, fix its key, confirm bursts stop, resolve aq#846/#847.
- T2 P0 aq RESPONDER tier: escalation rule (CRITICAL + repeats≥3 + no ack → auto P0 ticket +
  single page + suppress repeats). The LPP 152-page case is the acceptance test.
- T3 P1 Gatus content assertions for every CF hostname + add gameboy icmp + kiosk stream
  endpoint (discover port from src/stream.ts). Closes crossed-hostname + kiosk-death gaps.
- T4 P1 stop/restore contract: generalize RESTART_FREEZE — agents stopping any product unit
  must drop a machine-readable hold-down (owner ticket + restore-by); self-healer reads it:
  restore on lease-expiry, never fight an active flag. Closes the class that killed LPP.
- T5 P1 dispatcher health single-owner: remove W7's embedded health call; dispatch_health
  exits 0 on alarm; verify failed-unit echo stops. Also investigate the CURRENT alternating
  spawned=0/candidates=18 verdict — 69 STALLED alerts may be a live dispatch defect.
- T6 P2 fold maat-watch, a5-health, brain-health into sentinel probes; retire their timers.
- T7 P2 retire lumen-watchdog after backfilling its device list (incl. bes/sobek/seshat/
  gameboy/apep TS-IP facts into lumen_state, per the known-gap comment) into Gatus.
- T8 P2 self-healer declarative watch table + absorb smart-watchdog stall rule.
- T9 P2 alert hygiene: jay/github/ebay/System-map/per-CE-ECC → digest topic; repo-sentinel
  questfree/freemiko allowlist fix; probe-state-drift must-be-active list sync (lumen-agent).
- T10 P3 invisible-work watch: probe for wq tickets open>48h never claimed/dispatched + aq
  open-backlog (211) weekly rollup. Closes the "50 invisible tickets" class.
- T11 P3 (R3 overlap) delete night-watch.sh from disk; lumen-monitor STATUS.json references
  retired units (lumen-orchestrator/builder) — retire or fix.

## 8. ACTIONS TAKEN THIS SESSION (full-autonomy, all verified)
1. lumen-pokemon-live RESTORED 2026-08-26 19:58Z (12:58 PDT) via lpp-restart (slock taken/
   released, fresh event verified) + re-enabled for boot. Rung qwen36-local verified first.
2. Duplicate wq-notify watch unit …505 stopped; …542 remains as the single logger.
3. Report written; tickets seeded; episode committed. dispatch-deadman confirmed already
   retired (O2/O3 stale on that line).

## 9. EVIDENCE POINTERS
Watcher code paths in §1 table (all read this session). Noise: alerts.db 7d queries.
401 cadence: /home/lumen/logs/litellm/auth-failures.jsonl (uniq -c per minute). LPP: wq#863
lease EXPIRED@2026-08-23 16:15:09Z; events gap 08-23 16:04 → 08-26 19:58; probe FAIL count
152 (aq#836). Gatus tunnel conditions: bootstrap/sentinel/config/config.yaml (STATUS<500).


---

## 10. VERIFICATION PASS — wq#923 (session wq923-r2-7367d5, 2026-08-27 ~13:30Z)
Independent re-proof of §0–§9 against the LIVE system on the day after authoring, plus
formal closure of the wq#923 ticket the authoring session (r2-2fdc9f) never claimed. Method:
re-ran the load-bearing claims from primary sources; did not trust the prose. Result: report
stands. One NEW live defect found and ticketed.

### 10.1 Closed migration tickets — fix HELD (re-proven)
- **T1 / wq#964 (401 burst fix)** done 2026-08-27 11:40Z. PROVEN HELD: auth-failures.jsonl has
  ZERO rows dated 2026-08-27; last burst 2026-08-26T21:49:50Z. ~16h across every :19/:49
  window that previously always fired = fix landed, bursts stopped.
- **T2 / wq#965 (aq RESPONDER tier)** done 2026-08-27 12:21Z. PROVEN LIVE: lumen-aq-escalate.timer
  active (10-min); last run 13:28Z "1 eligible critical row; 0 seeded, 0 paged, 1 already-suppressed"
  = the missing responder tier now exists and is correctly de-duping. This is the single most
  important gap the report identified, now closed.

### 10.2 §0 headline live-fix — LPP holding (re-proven, incl. the frozen-but-active mode)
lumen-pokemon-live = active + enabled. Not merely unit-active: probe-pokemon-liveness rows are
all `resolved` (latest 2026-08-27T11:06Z), the 152-count outage row resolved — so it is NOT
re-frozen. The exact §0 failure mode (frozen-but-active) is absent.

### 10.3 Still-open GAPS — confirmed real and unremediated
- Gatus kiosk/gameboy coverage (T3/wq#966): sentinel/config/config.yaml has **0** gameboy/kiosk/
  stream matches. Gap real.
- Crossed-hostname (T3/wq#966): **3** tunnel conditions are STATUS<500-only, no content/body
  assertion. Gap real — ttyd-answering-200-on-chat-hostname would still PASS.
- probe-state-drift false-alarm (T9/wq#972): lumen-agent inactive (correctly retired) yet
  probe-state-drift still carries an open aq row — watcher still alarming on policy, not fault.
- aq open backlog = **211**, unchanged from authoring. O4 (intervention ledger) unaddressed.

### 10.4 NEW LIVE DEFECT (not in §2 rows) — filed wq#1011
foreman-health now FAILs every pass on a lane/config mismatch the report's §2 didn't capture:
**wq#709 [fleet-safety] "build lumen-guardian"** (a systemctl-poweroff thermal/MCE daemon) is
open+orphaned (lease expired 2026-08-23 09:42Z) in lane `fleet-safety`, which is ABSENT from
foreman/dispatcher/config.json → invisible to dispatch (never auto-worked, 7d) AND a perpetual
foreman-health FAIL + aq LANE/CONFIG DRIFT row. This is a **second live instance of the report's
central thesis** (detection works — foreman-health catches it every pass — but nothing responds),
exactly like LPP. It also refines §2 row 8: the invisible-ticket class is now partially DETECTED
(lane-mismatch subclass) but still unRESOLVED. Not fixed in-turn on purpose: lumen-guardian
issues `poweroff` with no auto-restart, so making it dispatcher-visible = unsafe silent
auto-dispatch. wq#1011 seeks a first-class "manual/gated lane" the dispatcher skips by design
(so foreman-health stops false-FAILing) and routes #709 there for a deliberate, reviewed build.
Sub-defect folded into wq#1011 (not spawned, per R2 anti-proliferation): `wq reap` only sweeps
status='claimed', so it cannot clear #709's open+stale-claimed_by ghost.

### 10.5 Verdict
§0–§9 verified. Consolidation design (§6: 26→~8, one page path, responder) endorsed unchanged;
the two highest-value tickets (responder, 401-fix) are already shipped and holding. Net new work
from this pass = wq#1011. wq#923 CLOSED.
