# Foreman dispatch + governor: how a ticket becomes a worker (wq#1667)

Read against foreman HEAD 5f0da1b, 2026-09-10 07:00Z, with the live dispatcher.log,
governor state.json and _runs/ from the fast-burn window Frank ran that night.
Every claim below cites a file:line or a log line; nothing is from memory.

Files that decide:

| file | role |
|---|---|
| dispatcher/foreman_dispatcher.py | the pass: scan -> gates -> spawn (1492 lines) |
| dispatcher/config.json | lane allowlist, per-pass caps, cloud ceiling, tight-mode block. READ FROM GIT HEAD (sec 3) |
| dispatcher/psi_pressure.py | /proc/pressure/memory -> ok / tight / deny |
| dispatcher/pacing.py + pacing_config.json | forward-looking weekly burn projection (policy=off since 08-23) |
| dispatcher/circuit_breaker.py | pass-level dead-man: failure rate over 90m from _runs attempt files |
| dispatcher/runaway.py | per-ticket: >=5 attempt files without done -> runaway_stuck; HARNESS_DEFECT hold |
| governor/foreman_usaged.py | daemon polling /api/oauth/usage -> foreman/state.json; `can-dispatch MODEL` |
| governor/governor_core.py | pure floor evaluation |
| governor/floors.json | floors (ALL ZERO since 2026-08-23) |
| orchestrator/router.py | archetype -> (model, effort) chain; `governor_pressure()` is what makes a pass "tight" |
| harness/spawner.py, harness/concurrency.py | `dispatch_one` -> `lumen-run start`; live fworker-wq* unit listing |
| harness/fworker.py | the worker; `trigger_refill` kicks a new pass on exit |

## 1. The decision path, step by step

One pass = one run of `foreman_dispatcher.py run --once` as lumen-foreman-dispatch.service.
The pass never claims a ticket and never runs a model turn; it only spawns detached
`fworker-wq<ID>` units and updates dispatcher/state.json counters.

### Pass-level (run_pass, L1414-1476)

0. kill switch: /home/lumen/autonomy/DISPATCH_DISARM or /home/lumen/foreman/STOP present -> halt, exit 0 (L392-399).
1. load_config: `git show HEAD:dispatcher/config.json` (sec 3). Structural errors fail closed; a bad lane is disabled and the pass continues (L209-277).
2. slock `foreman-dispatcher` ttl 30m; busy -> halt lock_busy (L402-409). This is the single-instance guarantee for refill kicks that overlap the timer.
3. circuit breaker (L1451): >=8 attempts in the last 90m with >=75% failing -> writes DISPATCH_DISARM and halts. Only counts attempt-N.json files (see sec 6).
4. `pass_start` logged with config_sha, then `_do_pass`.

### Preamble inside _do_pass (L1098-1155), once per pass

- scan_candidates (L719-745): `wq list <lane> --status open --sort effective` for every lane with enabled:true, in lane-name order; the merged list is sorted by (effective_pri, id). So a p1 in `research` beats a p2 in `build`; aging (wq#661) is respected across lanes.
- runaway.stuck_ticket_ids and harness_defect_ticket_ids (one scan each).
- PSI (L1129-1145): psi_pressure.check reads /proc/pressure/memory avg60. Thresholds come from config.json tight_mode: full>5.0 -> deny (halt whole pass, ntfy), some>25.0 -> deny, some>10.0 -> tight. The module defaults are 5/15/0 but the live config overrides to 10/25/5 (module docstring is stale). psi_unavailable -> ok.
- pacing config load (policy=off -> every verdict ok).
- governor_limits(): raw `limits` from foreman/state.json for pacing.

### Per candidate (L1157-1405), in this exact order

Each candidate runs inside a try/except quarantine boundary (wq#1408): one bad ticket costs itself a `quarantined` skip, never the pass.

| # | stage | check | skip/halt reason | notes |
|---|---|---|---|---|
| 0 | kill_switch | DISARM/STOP appeared mid-pass | halt kill_switch | |
| 0b | spawn_cap | `spawned >= limit`, limit = tight_mode.max_spawns_per_pass if tight else max_spawns_per_pass | halt spawn_cap | checked BEFORE the ticket is read |
| 3 | lane_policy | state.lane_counts[lane] >= max_per_day (UTC day) | skip lane_cap | all lanes 999 except pilot=3 |
| 3 | frank_gate | lane in HUMAN_LANES (user, user-projects, frank-*) | skip frank_gated (structural) | liveness gate (wq#1478) may auto-close first |
| 3 | lane_policy | router.repo_for_lane(lane) empty and lane != auto | skip lane_unmapped (structural) | |
| 3 | scan | `wq show` (30s timeout); status != open | skip vanished / not_open | |
| 3 | lane_policy | router.repo_mismatch: title names a different repo | skip repo_mismatch (structural) | |
| 3 | lane_policy | router.repo_for_ticket (needs:repo tag, repo hint) else lane repo | skip lane_unmapped | |
| 3.7 | capability | capability_lint.derive; enforce mode: refused -> capability_denied_by_policy, unresolved -> needs_unresolved, capability.preflight fails -> cited_path_unreachable / node_unknown; else auto-write tags into `needs` | skip (all structural) + ntfy once/24h per (reason,ticket) | runs BEFORE holds so a held ticket still gets tagged |
| 3.5 | runaway | tid in stuck (>=5 attempt files, not done) | skip runaway_stuck (structural) + ntfy once/24h | attempt FILES only, see sec 6 |
| 3.5b | harness_defect | last attempt file classified HARNESS_DEFECT | skip harness_defect_hold | |
| 3.6 | nodispatch | title has [EPIC] / [DO NOT CLAIM] | skip nodispatch_marker (structural) | |
| 4 | frank_gate | title has [FRANK GATE]/[manual]/owner=frank; gate_type set with no frank_response; wq `gate` path clause unmet | skip frank_gated (structural) | no override flag exists |
| 5 | fire_approval | lane.requires_approval true and approvals/wq<ID>.approved absent | skip fire_unapproved | fleet-fires has requires_approval:false since 08-23, so inert today |
| 5b | fire_probe | lane == fleet-fires and detail has neither `fire_probe: {cmd,...}` nor an `aq row:`/`Probe:` reference | skip fire_no_probe (structural) | |
| 5c | hold | wq hold live (hold_by + hold_expires in future) | skip held (structural) | FOREMAN_IGNORE_HOLDS=1 disables |
| 6 | deps | any dep not status=done (missing dep counts as unmet) | skip deps_unmet (structural) | |
| 7 | concurrency | concurrency.list_running_units() fails | halt lumen_run_unreachable | |
| 7 | already_running | fworker-wq<ID> unit live | skip already_running | |
| 8 | budget | candidate_model: ticket.model else router.assign dry-run; no rung / DECOMPOSE -> route_unassigned (structural); ManifestError -> bad_archetype + ntfy | skip | |
| 8 | budget | governor_pressure(model) (cached per model per pass) == deny | skip governor_deny | |
| 8 | budget | pressure == tight -> pass flips tight (logged once) | | sticky for the rest of the pass |
| 8 | budget | if tight: spawned >= tight cap -> halt; cand.pri > tight_mode.min_pri -> skip tight_pri | halt / skip | min_pri=1, so p2+ never spawns while tight |
| 8b | budget | pacing.decide: delay/skip | skip pacing_delay / pacing_skip + ntfy per pool per 6h | policy=off -> never fires |
| 9 | concurrency | cloud (non-local-model) units >= config cloud_ceiling (10) | halt no_slots | local rung (deepseek-v4-flash, qwen36-local) excluded |
| 9b | preflight | harness_preflight.dispatch_gate_check(repo), cached per repo per pass: profile JSON unreadable -> profile_unreadable + ntfy/1h; hard-stale stamp -> in-pass reprove (retry ladder 0/30/120s under 420s) then spawn or skip reprove_failed/no_fresh_stamp; soft-stale -> reseal and continue | skip | |
| 10 | spawn | spawner.dispatch_one(tid, repo): re-reads ticket, router.assign (live write of model/effort/archetype) if unset, `governor_allows` (a SECOND can-dispatch call), router.dispatch_cmd -> `lumen-run start --name fworker-wq<ID> --mem 8G --mem-high 6G --cpu 400% --tasks-max 512 --oom-policy stop -- python3 fworker.py run ...` (fire-response wrapped in `timeout 7200`) | spawn / error spawn_failed | lane_counts++ and state.json written atomically after each spawn; 2-3s stagger |

pass_end reports candidates, spawned, tight, quarantined, dispatchable (= candidates minus structural skips; what the watchdog uses to tell a stall from noise), lint counts.

### What the fworker does that feeds back into the next pass

- `wq claim --id` under its own agent name; a CLAIM_ERROR (e.g. a declared subsystem slock held by another session) exits immediately.
- On exit, `trigger_refill` (fworker.py L2567) runs `systemd-run sleep 3; systemctl start --no-block lumen-foreman-dispatch.service`, so a pass follows every worker exit within seconds. The 20-min timer is only the backstop, and it currently carries a drop-in `fast-burn.conf` setting OnUnitActiveSec=5min (Frank, 2026-09-10).
- Attempt bookkeeping goes to _runs/wq<ID>/attempt-N.json, which is what runaway and the circuit breaker read.

## 2. Every throttle / gate and what trips it

| throttle | where | trigger | effect | tonight |
|---|---|---|---|---|
| DISARM / STOP | kill_switch L392 | file present | halt every pass; refill kicks also refuse | absent |
| circuit breaker | circuit_breaker.py | >=8 attempts in 90m, >=75% failed | writes DISPATCH_DISARM, halts | armed, 0/0 at the 05:00 gate |
| slock foreman-dispatcher | L402 | another pass live | halt lock_busy | serializes refill storms |
| PSI deny | psi_pressure L71-75 | full avg60 > 5.0 or some avg60 > 25.0 | halt whole pass + ntfy | not tripped |
| PSI tight | psi_pressure L77 | some avg60 > 10.0 | tight from candidate 0 | not tripped |
| governor deny | foreman_usaged can-dispatch -> governor_core.evaluate_dispatch | STOP; live rejected rate_limit_event not yet reset; state stale (>600s since last good poll, or daemon dead per updated_at); remaining < floor for session / weekly_all / fable_weekly_scoped | skip governor_deny per ticket | 0 tonight (floors are 0) |
| governor tight | router.governor_pressure L739 | (remaining - floor) < SOFT_MARGIN_PCT (15, hardcoded router.py:192) OR API severity in (warning, critical) | pass flips tight: cap -> tight_mode.max_spawns_per_pass, pri > tight_mode.min_pri skipped | tripped EVERY pass 05:30-07:01Z (sec 5) |
| per-pass spawn cap | L1148, L1330 | spawned >= cap | halt | hit every pass |
| cloud_ceiling | L1349 | non-local fworker units >= 10 | halt no_slots | never near it (3 units live) |
| lane max_per_day | L1163 | UTC-day count per lane | skip lane_cap | pilot=3 only |
| pacing | pacing.decide | projected weekly burn > 90/130% (conservative) | delay (pri>1) / skip | policy=off |
| runaway | runaway.py | >=5 attempt files, not done | skip forever + ntfy | 2 tickets |
| harness_defect hold | runaway.harness_defect_ticket_ids | last attempt HARNESS_DEFECT | skip | |
| capability pre-skips | stage 3.7 | policy refusal, unresolved needs, unreachable cited path, unknown node | skip + ntfy | 9 tickets/pass |
| holds | stage 5c | wq hold | skip | |
| preflight stamp | stage 9b | repo profile stale/unreadable | reprove or skip, ntfy | all fresh |
| governor_allows | spawner.dispatch_one | second can-dispatch right before lumen-run | silent None -> error spawn_failed | duplicate of stage 8 deny |
| fire runtime cap | router.dispatch_cmd | fire-response archetype | `timeout 7200` around the whole fworker | |

Two things are NOT dispatcher throttles even though they look like it: `CLOUD_CEILING=3` in
concurrency.py is only used by spawner.dispatch_batch and the CLI, never by the dispatcher
(which reads config.json cloud_ceiling=10); the floors.json _note saying "only real limit is
cloud_ceiling=3" is stale on that number.

## 3. Config from git HEAD (wq#772), and the other gotchas

- **config.json is read with `git show HEAD:dispatcher/config.json`** (L186-206). An uncommitted edit does nothing; `git stash`/`git clean` changes live policy; the sha is logged on every pass_start. Tonight's cap raise needed commit 8da3114 to take effect; the config.json.bak.* files are inert history.
- **Tight is sticky per pass and set by the FIRST cloud model checked.** Pressure is cached per model; the first candidate reaching stage 8 on a warning-flagged model flips the pass. There is no un-tight.
- **The tight cap is checked at the loop top before the ticket is read AND again inside stage 8**, so a pass that flips tight after already spawning more than the tight cap halts on the next candidate.
- **pri vs epri**: the tight_pri gate uses the raw `pri`, not the aged effective priority the scan sorted on. A p2 aged to ep1 sorts first but is still refused while tight.
- **Two can-dispatch calls per spawn** (stage 8 via router.governor_pressure, then spawner.governor_allows). The second can flip to deny between them; the result is a spawn_failed error line rather than a governor_deny skip.
- **router.select's effort-first downgrade under tight is dead on the dispatch path**: candidate_model calls router.assign(dry_run=True) and spawner.dispatch_one calls router.assign() with no pressure=, so select() sees everything as ok. Only the CLI `--pressure` flag exercises it.
- **Model on the ticket wins**: once router.assign has written model/effort onto the ticket (first live spawn), later passes never re-route it; a downgrade needs a hand edit or fworker's own fails-walk.
- **Refill kicks make the real cadence "every worker exit"**, not 20 min. Under a crash loop that is a pass every 1-2 minutes (sec 6).
- **Runaway and the breaker only see attempt-N.json.** A worker that dies before write_attempt_state is invisible to both (sec 6). The wq `attempt` column climbs but nobody reads it.
- **wq hold is checked after capability lint on purpose** so a held ticket still gets its needs tag written and can release later.
- **fleet-fires requires_approval hardcode was removed 2026-08-23**; stage 5 is inert unless config sets it true again.
- **PSI module defaults (5/15/0) differ from live config (10/25/5)**; the psi_pressure.py docstring describes the old numbers.
- **Local-rung workers do not count toward cloud_ceiling** but DO count toward the per-pass spawn cap and are subject to tight_pri.
- **state.json lane_counts reset at UTC midnight**; ntfy dedup ledgers persist 24h (72h for pacing).
- **A structural skip is counted as not-dispatchable**; a new skip reason not added to STRUCTURAL_SKIP_REASONS makes the health watchdog page a fake stall.

## 4. The governor's numbers, live

foreman/state.json at 07:00:41Z: session 80% (resets 07:40Z), weekly_all 83% (resets 11:00Z), fable_weekly_scoped 86% (resets 11:00Z), every bucket severity=warning, source=primary, stale=false, floor_breaches=[], live_blocks={}.

floors.json: every floor 0, off-peak disabled, stale_fail_closed 600s. So evaluate_dispatch allows every cloud model (remaining >= 0 always) and can-dispatch only denies on STOP, a live rejected event, or stale state.

router.governor_pressure then computes, per pool: remaining - floor < 15 -> tight; severity warning/critical -> tight.
- session: 20 - 0 = 20, not < 15; severity warning -> **tight**
- weekly_all: 17, not < 15; severity warning -> **tight**
- fable (fable models only): 14 < 15 -> **tight** even without the flag

With floors zeroed, the API's own "warning" label (which appears around 80% used) is what throttled the fleet all night.

## 5. The tight-mode clamp we hit, and how spawn rate should scale

What happened (dispatcher.log, 2026-09-10):

- 05:30-06:27Z: every pass logs `tight_mode model=claude-sonnet-5 reason=pressure tight: max 1 spawn(s), pri<=1 only` then `halt spawn_cap 1/1 tight`. Passes ran every 1-5 min (refill kicks + 5-min timer): ~1 spawn per pass, 50 spawns in the 05 hour.
- 06:27Z commit 8da3114 raised tight cap 1 -> 4 (root 5 -> 8). From 06:32Z every pass is `4/4 tight`; 36 spawns in the 06 hour.
- The whole time: 3 fworker units live, cloud_ceiling 10, weekly budget 17% remaining with 4h to reset, 63-75 dispatchable candidates per pass, 4 tickets skipped tight_pri.

Why the clamp is wrong here: tight mode exists to conserve a budget about to run out. On a night where the budget resets at 11:00Z and Frank wants the remainder spent, a pool at 83% with 4h left is not scarce, it is expiring. The reactive rule ("throttle when little is left") has the sign backwards near a reset: the closer the reset and the more that remains, the faster we should spawn.

The two inputs that fire the rule are both outside the floors Frank zeroed: SOFT_MARGIN_PCT=15 is a constant in router.py and the severity flag comes from Anthropic's API. Zeroing floors.json did not zero the throttle; it only zeroed `deny`.

How spawn rate SHOULD scale (design sketch for wq#1669):

    remaining_pct        = 100 - percent (per pool the model draws on; min over pools)
    time_to_reset_frac   = (resets_at - now) / window_len          # 5h session, 7d weekly
    burn_budget          = remaining_pct / max(time_to_reset_frac, eps)   # spendable rate
    avg_quota_per_worker = measured from _runs (pool-% delta per completed attempt, EMA)
    allowed_concurrent   = clamp(burn_budget_per_hour / avg_quota_per_worker_per_hour, 1, cloud_ceiling)
    allowed_this_pass    = max(0, allowed_concurrent - running_cloud_units)

Properties: with 17% left and 4h of a 168h window, burn_budget is ~7x the steady-state rate, so the cap opens to the ceiling; with 17% left and 100h to go it clamps down; at 2% left with 4h to go it still allows a couple of workers because the quota expires anyway. The session pool (5h window) dominates during a session crunch; the weekly pool dominates mid-week. The API severity flag becomes advisory (logged, ntfy once) unless a floor is set. `deny` stays as-is (STOP, live rejected event, stale state, real floor breach).

Until that ships, the honest knob is the one used tonight: tight_mode.max_spawns_per_pass and min_pri in config.json, committed. A cheaper interim: read SOFT_MARGIN_PCT and a `severity_throttles` bool from floors.json, default to today's values, and set both to 0/false when floors are zeroed.

## 6. What actually ate the slots tonight (found during this walkthrough)

The per-pass cap (1, then 4) was the ceiling, but two tickets consumed a slot every pass:

- **wq#1543 spawned 31 times and wq#1572 23 times between 05:00 and 07:01Z**; ticket attempt counters read 73 and 38. Journal: each spawn does fire-mode re-verify, finds the probe CLEARED, calls `wq done` with notes that carry no artifact path (evidence=None for the legacy aq-row probe form), the wq#759 evidence gate refuses, `wq_done` raises, fworker exits CRASH (15) after ~2s, the claim releases, trigger_refill kicks a pass, and the dispatcher spawns it again. 118 `wq done N refused (exit 2)` journal lines in 3 hours.
- **wq#1623 spawned 6 times**: fworker CLAIM_ERROR because subsystem `litellm` is slocked by work-1580. The dispatcher never checks slocks before spawning.
- None of these wrote an attempt-N.json, so runaway (>=5 files), the circuit breaker (attempt files in 90m) and the harness-defect hold were all blind to them. The `attempt` column on the ticket saw every one.

Net: during the 1-cap window roughly half the passes spent their single slot on a 2-second crash. During the 4-cap window, two of every four.

Actions taken in this session (bandaids, labelled as such): holds placed on 1543 and 1572 for 12h by work-1667-8525a6 so stage 5c skips them (their probe condition really is cleared; close them properly once the fix lands). Tickets filed:

- wq#1670 (P0) fworker fire auto-close must pass `--no-artifact` when there is no evidence path, and a refused `wq done` must charge an attempt and hold/block instead of crash-looping.
- wq#1671 (P1) crash-before-charge blindness: dispatcher reads the wq `attempt` column, fworker charges an attempt on every exit path, refill does not kick after CLAIM_ERROR/LAUNCH_FAILURE, dispatcher pre-checks subsystem slocks.
- wq#1669 (P1) governor tight-mode ignores zeroed floors; burn-rate controller per sec 5; wire pressure= into the dispatch-path assign or drop the dead downgrade branch.

## 7. Tuning recommendation

1. Now (config, committed): keep tight cap 4 / root 8 for the burn window; revert to 1/5 after 11:00Z only if wq#1669 has not landed, otherwise let the controller own it. Raise tight_mode.min_pri 1 -> 2 while floors are zeroed: a warning-flagged pool with floors=0 is not scarce, and p2 is where the queue lives (63-75 dispatchable per pass, ~4 spawned).
2. Ship wq#1670 first: a 2-line fix (`--no-artifact` on the empty-evidence path) that recovers 25-50% of every pass's slots and stops the 1-2 min refill storm.
3. Ship wq#1671: charge every exit, and read the ticket `attempt` column in stage 3.5. This is the class fix; without it the next crash-before-charge bug is invisible again.
4. Ship wq#1669: burn-rate controller replacing the binary tight flag; SOFT_MARGIN into floors.json; API severity advisory when floors are 0; wire or drop the effort downgrade.
5. Leave PSI, breaker, cloud_ceiling, holds and capability gates alone: none of them limited anything tonight and each does what its ticket asked.
6. Fix the psi_pressure.py docstring to the live 10/25/5 numbers and the floors.json note (cloud ceiling is 10 in the dispatcher, 3 only in the spawner CLI).
