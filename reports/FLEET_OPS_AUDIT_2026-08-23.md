# FLEET OPS AUDIT — 2026-08-23 (session fable-opsaudit-83c6ab)
## Question: is everything actually connected and firing? Evidence grades: A=observed/reproduced live, B=log/artifact, C=config-read.
STATUS: IN PROGRESS — Tier 1 substantially complete; Tier 2 underway. Checkpoint 16:30Z.

## THE SILENT-FAILURE REGISTER (headline — ranked by how long it could go unnoticed)

| # | Component | Failure mode | How long unnoticed | Grade | Status |
|---|-----------|--------------|--------------------|-------|--------|
| S1 | litellm-auth-monitor | `docker logs --follow` ENDS on container restart → clean exit 0 → Restart=on-failure never fires. Died 03:29Z, manually revived 15:20Z (~12h, second occurrence). Script's "reconnects automatically" comment is FALSE. | Indefinite (until next auth incident) | A | FIXED unit layer: Restart=always (bak'd). Monitor itself proven A-grade: injected 401 captured <4s. |
| S2 | auth-failures.jsonl | NOTHING consumes it. Auth outage = logged, never alerted. Live unexplained 401s from 172.20.0.1 at 16:19:49Z. | Indefinite | A | wq#911 seeded. |
| S3 | wq-drift-guard | Crashed every 5 min since 13:23Z (`set -u` + no argv). The guard shipped YESTERDAY to catch wq clobbers was dead on arrival — every scheduled run failed. | 3h (failed-units sweep DID aq/ntfy it at 13:27 — but alert sat in aq unactioned) | A | FIXED (argv guard), committed bin@5c9dc58, clean run + full e2e drift-injection test PASSED (detect→ntfy 200→aq→--fix restore). Class-swept bin/: no other instances. aq#820/821/827 resolved. |
| S4 | Kiosk :8871 | systemd unit pointed at NONEXISTENT /home/lumen/kiosk/serve.py → exit 0 in 1s, never restarts. Live server was an UNMANAGED ad-hoc `python3 -m http.server` (pid 2458027, started 16:06Z post-incident) — would die on any kill/reboot with no restart. | Until next reboot/crash, then indefinite | A | FIXED: unit rewritten to serve /home/lumen/pokemon/assets, Restart=always, enabled; ad-hoc killed; kill -9 test → auto-restarted in 10s, HTTP 200 on LAN face. NOTE for wq#907/908: docs expect :8871/progress.json when LPP live — must land in assets dir. |
| S5 | 27 tickets in 7 unconfigured lanes | auto/fleet/fleet-spend/ops/pokemon/quest-repair-research/research: open tickets the dispatcher can NEVER dispatch. Health check detects (aq#813 ×10) but nobody acts — detection without remediation. Never in config git history = mis-laned at creation. | 41d+ (oldest ticket age unknown; alarm ignored ×10) | B | wq#912 seeded (disposition pass + creation-time lane guard). |
| S6 | Restart=on-failure daemon class | 8 more daemon units shared the S1 latent defect (dashboard, guardian, orchestrator, persona-engine, vip-api, vip-watcher, kiosk-idle, builder): any clean exit = permanent silent death. | Latent | A | ALL flipped to Restart=always (bak.20260823_16*), daemon-reload, all stayed active. pokemon-live left untouched per Frank order (still inactive+disabled, verified). |
| S7 | dispatch-deadman residue | Script retired 02:28Z (wq#825) but transient unit kept 203/EXEC-failing until 05:40Z. Replacement dispatcher_health IS live and firing (foreman-health timer, "OK — last 3 passes" 15:30Z) BUT its code rides PR#80 UNMERGED — clean checkout loses the deadman again (wq#899 tracks). | n/a (replaced) | B | No action; wq#899 owns PR#80 merge. |
| S8 | ntfy OOB backup path | Live repro during drift test: `delivered [primary=200 oob=429(FAIL)]` — the backup alert path is rate-limited in practice. Primary works. | Only matters when primary fails — then silent | A | Known wq#901; live evidence recorded here (wq CLI has no note verb). |
| S9 | brain_healthcheck | Reports "OK — backend=anthropic, claude CLI HEALTHY" but production Messenger brain = DeepSeek V4 via LiteLLM. Probes a path production doesn't use → green forever regardless of real brain. | Indefinite | B | Known wq#781, confirmed still live 16:18Z. |

## TIER 1 FINDINGS TABLE
| Component | Should do | Evidence it did | Last-did | Grade | Action |
|---|---|---|---|---|---|
| LPP (lumen-pokemon-live) | STAY DOWN (Frank order) | inactive + disabled | — | A | verified, untouched |
| Timer fleet (133) | fire on schedule | schedule-aware sweep: 0 stale after period correction (backup-verify is MONTHLY, fired 08-01 on time; retirement-audit/trivy-weekly installed today, first fire pending — not defects) | per-timer | B | timer_stale3.txt in scratch |
| Timer→service results | last run rc=0 | sweep of all timer services: 3 nonzero → wq-drift-guard (S3, fixed), repo-sweep (pstack, wq#913), repo-visibility-sentinel (violation-by-design, RealLumenHere/questfree = wq#902 on Frank) | 16:1xZ | A/B | 1 fixed, 1 ticketed, 1 known |
| failed-units sweep | alert on ANY newly failed unit | caught wq-drift-guard in 4 min (aq#820/821 + ntfy) | 13:27Z | A | works; per-unit OnFailure coverage (3/103) is redundant given this sweep — not a defect |
| foreman selfheal | requeue INFRA_CRASH | event log 16:17:50Z | 16:17Z | B | ok |
| foreman-health / dispatcher_health | alarm on dead dispatcher | "OK — last 3 passes" w/ spawn evidence 15:30Z; e2e config-error test done by wq#825 | 16:21Z | B | ok; PR#80 merge risk (S7) |
| model-watchdog | probe rungs | probed=7 down=none incl qwen36-local local_ok | 16:20:54Z | B | ok |
| lumen-watchdog | node liveness + ntfy on change | watchdog.log live (mtime 16:20) | 16:20Z | B | ok |
| sentinel-probes | storage integrity 12 nodes | PASS 12/12 sla=0 | 16:10:44Z | B | ok |
| lumen-sentinel-watch | alert if Gatus down | rc=0 = Gatus API answered | 16:17Z | B | failure path = plain ntfy (proven elsewhere) |
| night-watch | (retired concept) | replaced by dispatcher_health per wq#825 | — | B | ok |
| ntfy primary | deliver | 200 live ×2 (drift test + ticket seeds) | 16:19Z | A | ok |
| aq queue | capture alerts | rows created live | 16:19Z | A | but 198 open rows = alerts die unread (DELIVERY_FAILURE class, D9) |

## SESSION ACTIONS LOG (restore invariant)
- Patched+committed /home/lumen/bin/wq-drift-guard (bak kept). Injected wq drift ~16:19:48Z, restored via --fix within seconds, wq verified functional.
- 9 unit files edited with .bak.20260823 siblings (8 Restart=always + kiosk rewrite); daemon-reload; no service interrupted except kiosk (~3s swap + deliberate kill test, auto-recovered).
- Ad-hoc http.server pid 2458027 killed — REPLACED by managed unit (this was the restoration, not an outage).
- aq resolved: 820, 821, 827. Tickets seeded: wq#911, #912, #913 (ntfy'd at creation).
- Nothing currently stopped. Thor untouched.

## TIER 2 — LiteLLM gateway (the single most load-bearing dependency)
| Check | Evidence | Grade | Disposition |
|---|---|---|---|
| Gateway alive | /v1/models 200, chat 200 via capped key | A | ok |
| Per-key auth enforced | injected bad key -> 401, captured by auth-monitor <4s | A | ok |
| Virtual keys | 12 rows, 0 expired, 0 blocked, 0 null-alias | B | ok |
| Fallback termination | 4 fallback groups, all terminate in cloud rungs. **DANGER CLASS: 6 local rungs with timeout=600s and NO fallback** (qwen36-local, isis-qwen36, isis-qwen3-32b, gemma4-e4b-local, qwen38-9b-distill-local, ternary-bonsai-27b) — a saturated backend blocks a caller 10 min/attempt = the LPP-freeze class. Fallback absence is a RECORDED Frank decision (anti-silent-reroute, HANDOFF item 5) — evidence to wq#907, no config change made. | A(config)+B | wq#907 owns |
| Master key retired for LLM routes | **VIOLATED — wq#914 P0.** True master (container env) = 200 on chat, NO DB row. Contradicts service_keys.md + FABLE_AUDIT 15:15Z 401 repro. Suspect fail_closed_budget_enforcement comment-out (config:339, ~14:54Z). | A | wq#914 seeded, ntfy urgent |
| agent/.env master var | silently repointed to capped 'ops-emergency' key (02:48Z, $25/30d, 1775 calls, no episode record) — bridge for wq#903 hardcoded callers, undocumented | A | documented in wq#914 |

## TIER 2 — storage / backups (verified by SNAPSHOT, per brief)
- Both repos live: local 117 snapshots, /data mirror 115. Latest important 5.2h old, latest critical 0.5h old.
- **backup-critical proven hourly + green**: journal 14:03/15:02/16:03Z all "DONE errors=0"; sparse critical list = retention pruning, NOT missed runs (initial 9-day-gap read was a false alarm — narrative killed).
- **pokemon captured** (wq#556 fix HELD): critical snapshots carry /home/lumen/pokemon/{checkpoints,runtime,saves}; daily important carries /home/lumen/pokemon/events; snapshot 835be41b confirmed present in local repo. My first "--path /home/lumen/pokemon = zero snapshots" was a query artifact (exact-path match) — killed.
- claude_memory.md, /home/lumen/decisions, secrets, minerva learnings, staged DB dumps all in latest critical snapshot paths. Grade A(snapshot-read)/B.
- backup-verify is MONTHLY (Aug-1 fired on schedule, next Sep-1); its Aug-1 journal is rotated — that run's success is grade C. Residual.

## TIER 2 — cloudflared ingress (27 rules, crossed-wires class)
- All 26 hostnames probed public+origin. chat->8765 VERIFIED Messenger (health JSON w/ ws_clients — not ttyd). status->Gatus "Lumen Fleet Sentinel". home->Glance "Lumen Fleet". mctl->"Mission Control". pokemon->kiosk (matches local). No crossed hostnames found today.
- Access wall present on all sensitive apps EXCEPT: **memory-mcp.lumennode.uk — NO Access, origin answers anonymous MCP protocol traffic (wq#915, ntfy'd)**. law/actual/pokemon public-by-apparent-design (actual.lumennode.uk = finance app on its own auth — review item, listed under Frank's eyes below).
- vip origin 404-at-root is by design (/health + /docs 200). MCP origins 405-at-GET normal.

## TIER 2/3 — nodes (SSH sweep)
- OK + healthy disk/inodes/NTP: apep 5%, bes 4%, isis 28%, maat 3%, thoth 7%, anubis 7%, sobek 11%. Uptimes 21-33d (staged kernels unbooted = known wq#418, Frank-gated).
- **apis: tailnet active, SSH:22 dead x2 — wq#916.**
- Tailnet OFFLINE: mirror (Surface Go), seneca, desktop-u520obe — transient/parked plausible, intent NOT verified (residual).
- Thor: tailnet active;direct. NOT touched per standing order.

## DEPENDENCY MAP (what breaks if X dies)
- **LiteLLM :4000** → Messenger brain, all lane LLM calls, sentinel probe-synthetic-llm, LPP brain (when revived). Fallbacks exist for cloud rungs only; 6 local rungs block 600s with no fallback (wq#907). Detection: model-watchdog (proven probing) + auth-monitor (now Restart=always) — but auth failures still alert NOBODY (wq#911).
- **Isis :11434** → qwen36-local + isis-* rungs; single-slot saturation can block callers (wq#898). model-watchdog probes it.
- **Postgres lumen-pgvector** → LiteLLM keys/spend (gateway falls to... untested), Minerva. Backed up hourly.
- **cloudflared** → all 26 public hostnames incl vault + Commander path. Change-control rule already standing (detached systemd-run only).
- **Commander MCP** → Claude's own hands on the fleet. Rides cloudflared; stateless unit (do not change).
- No-fallback singles: Gatus (sentinel-watch alerts if down — proven reachable), ntfy primary (OOB backup 429s = wq#901 → effectively NO working backup alert path).

## ALERT-PATH COVERAGE MATRIX
| Failure | Reaches Frank? | Via | Proven |
|---|---|---|---|
| Any systemd unit newly failed | YES (ntfy+aq <5min) | lumen-failed-units | A (wq-drift-guard case, 4 min) |
| wq binary clobbered | YES | wq-drift-guard (fixed today) | A (live injection) |
| Dispatcher dead/erroring | YES | dispatcher_health via foreman-health | B (wq#825 e2e) — code UNMERGED PR#80 (wq#899) |
| Model rung down | YES | model-watchdog | B |
| Gatus down | YES | lumen-sentinel-watch → ntfy | B |
| **LiteLLM auth failures** | **NO — logged only** | (none) | A — wq#911 |
| **ntfy primary down** | **NO — OOB path 429s** | (none working) | A — wq#901 |
| Gatus send-on-resolved ext/heartbeat | NO | known wq#667 | C |
| aq queue itself (198 open rows) | Alerts LAND but DIE unread | D9 delivery class | B |

## COULD NOT VERIFY (honest residual)
1. wq#914 mechanism: WHY true master 200s now vs 401 at 15:15Z — forensics unfinished, P0 seeded.
2. Per-service virtual keys live-tested only via DB validity + gateway auth both-ways; each caller's own key not individually exercised.
3. backup-verify Aug-1 run success (journal rotated) — next fire Sep-1.
4. Gameboy-pc SCREEN content (Firefox kiosk) — server side proven 200 on the exact URL it points at; the rendered display needs eyes/reboot of browser if stale.
5. mirror/seneca/desktop-u520obe offline: intent unconfirmed.
6. Tier 3 items 10/12 (dispatcher deliberate-break test, full config-drift sweep beyond dispatcher+bin) — NOT REACHED. Deliberate dispatcher break skipped: workers were actively spawning (15:00-15:17Z passes) and a break test risks colliding with live wq#896-class work; dispatcher_health already has a same-day e2e injection proof from wq#825.
7. Thor: everything (standing order).
8. The 33 containers beyond litellm/pgvector: liveness only via their fronting services in the cf sweep — no per-container forward-progress pass.

## ITEMS FOR FRANK'S EYES (not gates — FYI/decisions)
- actual.lumennode.uk (finance) is public with only app-level auth while every peer sits behind CF Access — deliberate?
- ops-emergency bridge key: sane design, zero documentation trail — wq#914 will write it into service_keys.md.
- wq#912's 27 invisible tickets include lanes that may be deliberately manual (research, pokemon) — disposition pass will ask per-lane.
