# Lumen reliability roadmap

- ticket: wq#1687 (build, p1) · agent `work-1687-e77bb0` · router `claude-opus-5 @high` (research-synthesis)
- written 2026-09-10 03:15-03:45 PDT · synthesis only, no code or config changed
- inputs: `FLEET_SERVICE_ATLAS.md` (wq#1627), `MINERVA_PIPELINE_MAP.md` (wq#1626),
  `MINERVA_STALENESS_REVIEW.md` (wq#1607), `DISPATCH_GOVERNOR_WALKTHROUGH.md` (wq#1667),
  `TICKET_CLOSURE_AUDIT.md` (wq#1668), `FLEET_COST_PLAN.md` (wq#1586),
  `pokemon/foreman/research/LPP_ARCHITECTURE_REVIEW.md` (wq#1595),
  `pokemon/foreman/research/LPP_BEHAVIORAL_ANALYSIS.md` (wq#1628), the `reeval_0*.md` set,
  tonight's archival episodes, and `queue.db` as of 10:30Z
- every "live" claim below was re-checked against the box between 10:15Z and 10:30Z UTC
  (commands in §6). Where a source doc said something is done or healthy and the box
  disagrees, the box wins and §0.3 says so.

Short names used below: ATLAS, MAP, STALE, WALK, CLOSE, COST, LPP-ARCH, LPP-BEH, REEVAL.

---

## 0. Read this first

### 0.1 The fleet was down while this was written

At 10:21Z the Claude Max OAuth session on Lumen was logged out (`claude auth status` ->
`loggedIn: false`). The refresh token expired at 02:25Z; the CLI blanked
`.credentials.json` on a failed refresh at 08:51Z. Consequences, all verified:

| surface | state | why |
|---|---|---|
| messenger brain | down since 08:52Z (aq#1178, wq#1686) | `claude CLI exit=1` |
| foreman dispatcher | `spawned=0` every pass since ~08:10Z, 35-39 dispatchable, 0 fworker units | 37/37 skips `governor_deny`: usage poll 429 -> state stale -> `can-dispatch` fail-closed |
| pages | "DISPATCHER STALLED" x3, "probe-brain-health" x3, two auto fleet-fires tickets | none named the cause |

Physical fix is Frank's (wq#1693, liveness-probed so it closes itself). The class fix is
wq#1694. This incident is not a side note: it is every pattern in §1 happening at once, and
it was found by a synthesis ticket reading state for context, not by any detector. §5 M3.

### 0.2 The answer in one paragraph

Tonight's audits describe six surface problems (stale-fact drift, false-complete tickets,
dead guards, the dispatch clamp to 1, LPP freezes, alert cry-wolf). They are not six
problems. They are eight recurring patterns (§1) produced by six root causes (§2), and the
six root causes reduce to one habit: **the fleet records what it intended and what it
noticed, and treats both as what is true.** A merged PR is treated as a deployed fix, a
label as an enable state, a YAML as the box, a missing alert as health, a restart as
progress, a report as research. The fixes that matter are not new detectors. They are
contracts that make a claim carry its evidence: done carries a deploy receipt, a gate
carries a probe, a credential carries its expiry, a condition carries one row, a fact
carries its last confirmation, a report carries its grounding. Most of those contracts
are already designed in the source docs and ticketed. What blocks them is §5: the fixes
run through the same dispatcher, capability policy and `wq done` gate the audits found
broken, and at time of writing none of them could move.

### 0.3 Corrections to the source docs found while cross-checking

| doc says | box says (10:15-10:30Z) | ticket |
|---|---|---|
| CLOSE: 13 false-completes, all LPP roadmap PRs | also LPP PR **#108** (wq#1578, resume ping-pong fix) and **#112** (wq#1661, :8871 owner) are `open, merged=false` while their tickets are done. The two LPP freeze fixes are not running. | note on wq#1672 |
| wq#1609, #1632, #1658 `done` (contradiction scan, lifecycle closure, universal timer dead-man) | only staged under `foreman/ops/wq*/install.sh`. No `bin/lumen-contradiction-scan`, no lifecycle code in `minerva/`, 0 `99-cron-stamp.conf` drop-ins. A second false-complete class: merged is not installed. | wq#1696 |
| MAP §1.8: pgvector research index is "the healthiest store" | freshness is healthy, content is not: 6 of 7 `reeval_*` reports are fabricated and were indexed into `research_docs` at 10:04Z | wq#1695 |
| REEVAL set as synthesis input | not usable as evidence of anything except §1 P8: invented tables, services, repos, a $150k/month spend (COST measured $107.31 / 35 d), leaked model reasoning, a self-assigned APPROVED status. Only `reeval_06` is honest. None of their recommendations enter §3. | wq#1695 |
| ATLAS §5 #10 journald ~11 h retention | unticketed in ATLAS §7. The volume is supergateway logging full MCP tool bodies, which also puts anything a browser session prints into journald and onward to Bes. | wq#1698 |
| WALK §6 lists the fixes wq#1669/1670/1671, CLOSE lists wq#1672 | all four `blocked`: landing conflict (1670 vs 1671, same file), `FALSE_COMPLETE: no mechanical signature` (1669, 1671), `CAPABILITY_DENIED` on its own worktree and `/tmp` (1672) | wq#1697 |

---

## 1. Cross-cutting failure patterns

A pattern is listed only if it shows up in at least three independent sources. Each row is an
instance, not a finding to fix on its own.

### P1. Silence reads as success

A check that cannot decide returns "fine", and nothing records that it could not decide.

| source | instance |
|---|---|
| CLOSE F1 | `pr_merge_state` -> `unknown` (no GitHub token) -> warn and accept; 13 false closes |
| MAP §1.7, ATLAS §4.9 | memoryd client fails open to "" then the legacy router; federated index 25 d stale behind a flag that is off; "a fail-open client makes a broken integration look like a working one" |
| LPP-ARCH §4.3, LPP-BEH E6 | terminal rung "never skipped" but unreachable; 45,965 budget errors over 144.7 h logged as designed gameplay continues |
| ATLAS §3.3 | `lumen-split-watch` reports `ok` over 206 stuck + 187 dead; `fleet-inv drift` prints NO-FACT as success (MAP §3.3) |
| ATLAS §4.2-4.3 | ntfy up + aq subscriber dead = pages still arrive, aq silently stops filling; deadman beat still green |
| live, 0.1 | usaged logs "429, not treating as an outage", then ages to stale and fail-closes all cloud dispatch without a page that says so |

Why it recurs: fail-open is the fleet's correct default for availability (instinct: never let
infra trouble block work). What is missing is attribution: a fail-open path must stamp
which side answered. Every instance above passed its own tests.

### P2. Intent accepted as state

The artifact that says what should be true is read as proof of what is true.

| source | instance |
|---|---|
| CLOSE F1/F3/F4, 0.3 | ticket `done` on an unmerged PR, on a report the worker cited instead of the ticket's deliverable, on a path inside a deleted worktree, on a merged-but-staged ops bundle |
| ATLAS §0 #5, F-3 | six timers described "installed DISABLED / Frank-gated" are live and running |
| WALK §3 | dispatcher policy is `git show HEAD:config.json`; the file on disk is not the policy |
| MAP §3.3 #1, §3.2 | inventory YAML "is truth" for the same predicates the state store owns; `thoth.yaml` stamped `last_verified` over stale prose |
| LPP-ARCH F8 | `SYSTEM_PREFIX` tells a one-badge agent on Route 25 to leave Oak's lab; `STORY_ORDER` ends at Route 5 |
| MAP §2.1, HANDOFF | `claude_context.sh` header says ~2K tok (measured ~32K); HANDOFF §3 is July-era under a disclaimer; `psi_pressure.py` docstring and `floors.json` note state old numbers (WALK §3) |

Why it recurs: writing the intent is cheap and happens in the same change as the work;
checking the box is a separate step owned by nobody.

### P3. Coverage is opt-in, so it decays

Every safety mechanism protects only what someone remembered to register with it.

| mechanism | coverage today | source |
|---|---|---|
| liveness gate (auto-close resolved gates) | 5 of 79 gated/blocked/user tickets carry a probe | CLOSE F2 |
| cron dead-man stamps | 11 of 118 timers (vault HA and all backups uncovered) | ATLAS §3.1 |
| `OnFailure=` pages | 10 of 179 services | ATLAS §1.2 |
| runaway + circuit breaker | only tickets that wrote `attempt-N.json` (1543/1572 respawned 31/23 times unseen) | WALK §6 |
| aq escalation to a ticket | `source=probe` only; 240 of 250 open rows have no exit | ATLAS §3.1 |
| contradiction scanner | 6 of 312 superseded facts (IP-shaped only) | STALE §0 |
| healer table | ollama, gatus-ntfy-relay, guardian absent | ATLAS §5 #5 |
| LPP stuck modes | no `BRAIN_DOWN`, no `RESUME_FLAP`; `RESUME_DEADEND` unreachable during a cycle | LPP-ARCH D2/D3 |
| capability grants | a worker is denied `/tmp`, the scratch dir `wq claim` tells it to use, and its own worktree | wq#1672/1610/1616 |

Why it recurs: registration is a manual step at build time; the fleet grows faster than
the registrations, and an unregistered thing produces no signal about being unregistered.

### P4. Conditions modelled as events

A persistent state of the world is represented as a stream of fires, so it re-fires until
a human reads the pile.

| source | instance |
|---|---|
| CLOSE F5 | `[repo-sweep] bin: BEHIND_ONLY` filed and closed 9 times in 9 days |
| ATLAS §4.15 | 250 open aq rows: jay-watch 78, failed-unit 37, ntfy:lumen-alerts 84 |
| ATLAS §5 #1 | guards exit 1 to report a finding, so a finding and a crash both show red; 4 of 8 failed units were working guards |
| LPP-BEH E6, LPP-ARCH D1 | `supervisor_directive_loaded FIGHT_LOSS_LOOP` 9,200 times; `isisAlerted` latch re-fired 19 times because it lived in a process restarted every 57 s |
| WALK §6 | 118 `wq done refused` lines in 3 h from two tickets whose probe had already cleared |
| live, 0.1 | "DISPATCHER STALLED" x3 and "probe-brain-health" x3 for one logged-out session |

This is alert cry-wolf, stated as a design defect rather than a tuning problem.

### P5. State lives in the thing that restarts

Counters, baselines and evidence are held in a process or log that the failure itself
resets, so the loop erases the proof of the loop.

| source | instance |
|---|---|
| WALK §6 | fworker dies before charging an attempt; the dispatcher's counters never see it |
| LPP-ARCH W4/W5/D1 | failStreak reboot every ~57 s; no counter of sterile continues; run tag and supervisor ban baseline re-minted per Agent |
| ATLAS §5 #10, 0.3 | journald ~11 h; mcp-pressure-guard trip history and the langfuse smoke failure reason already gone |
| ATLAS §5 #7 | 41 `Restart=always` daemons with no `StartLimitBurst` and no restart-count alarm |
| MAP §1.17 | 348 fworker `episode.jsonl` files that nothing distills or recalls |

### P6. Two owners, or no owner

| thing | owners | source |
|---|---|---|
| node IPs / ssh user / role | state store and inventory YAML, both claiming authority | MAP §3.1 |
| session handoff | `HANDOFF.md` (read, hand) and `kb/SESSION-HANDOFF.md` (generated, never read) | MAP §1.10 |
| cloudflared restart | healer, public-guardian ladder, wadjet-ha-monitor, no shared cooldown | ATLAS §5 #8 |
| RAM kills | node-killer, rosco, healer RAM-crit | ATLAS §5 #9 |
| dispatch permission | two `can-dispatch` calls per spawn; tight mode set by whichever model is checked first | WALK §3 |
| LPP gateway budget | four independent clients (brain, streamer, persona, vision) on one key | LPP-ARCH §4.4 |
| embeddings | bge-m3 for sections, nomic for everything else | MAP §4.7 |

### P7. Finite resources treated as booleans

Budgets, quotas and credentials are machine-readable countdowns. The fleet reads them only
as "works / does not work" at the moment it fails.

| source | instance |
|---|---|
| LPP-BEH §1 | ~202 of 336 frozen hours with no reachable brain: $5, $40 and $10 key caps hit, a key scoped to the wrong models, a renamed alias |
| WALK §4-5 | tight mode fires on the API's `warning` label at 80 % used, 4 h before a reset, while Frank wanted the budget spent: the clamp to 1 |
| ATLAS F-8/F-9, CLOSE F1 | jay-watch and the wq merge gate both broke on the wq#1581 token move; lpp-cerebras at 100 % |
| COST §1 | 81 % of forward spend is one workload (LPP); nothing forecasts its cap |
| live, 0.1 | `refreshTokenExpiresAt` sat in a JSON file for 6.5 h past expiry before anything failed |

### P8. Writes to trusted stores are unverified and unaudited

| source | instance |
|---|---|
| REEVAL, 0.3 | fabricated research indexed into the corpus the proposal hallucination guard uses as ground truth |
| MAP §1.1, §4.5 | `lumen_state.py ingest` and CLI `lumen_episode.py commit` write authoritative stores with no audit row |
| MAP §3.2 | a fix recorded as a new predicate (`thoth.tailnet_routing=FIXED`) leaves `thoth.status=OFFLINE` current |
| MAP §1.9 | `minerva_brief.py` writes `claude_memory.md` directly under a proposal-only law, unlabelled |
| STALE §0 | 10 of 830 open facts ever re-confirmed after ingest |

---

## 2. Root-cause clustering

Six underlying causes. The matrix shows which cause each surface problem actually rests on;
a surface problem with two marks needs both fixes before it stops recurring.

| root cause | what is missing | patterns |
|---|---|---|
| **RC-A** absence is not a signal | nothing asserts that an expected thing happened (a timer fired, a subscriber persisted, a fact was re-observed, a credential is still valid); fail-open paths do not say which side answered | P1, P3 |
| **RC-B** the artifact of intent is trusted | no contract that a claim of done/enabled/current carries evidence from the box | P2 |
| **RC-C** no durable condition object | a persistent condition has no single keyed row with open/close, count and owner; state is held in restartable processes and short logs | P4, P5 |
| **RC-D** no single owner | one fact, one actuator, one budget with several writers and no arbitration | P6 |
| **RC-E** finite resources are booleans | budgets, quotas and expiries are not modelled as countdowns with lead time | P7 |
| **RC-F** writes are neither verified nor audited | authoritative stores accept content without grounding or a trail | P8 |

| surface problem (tonight's themes) | RC-A | RC-B | RC-C | RC-D | RC-E | RC-F | the one-line mechanism |
|---|---|---|---|---|---|---|---|
| stale-fact drift (thoth, nephthys, storage manifest, inventory) | x | x | | x | | x | nothing re-confirms; YAML and store both claim truth; fixes land as new predicates |
| false-complete tickets | x | x | | | | | merge state unknown -> accept; merged/staged -> done |
| dead guard services | x | x | x | | | | guard exit convention conflates finding and crash; guards over retired systems report ok; 107 timers without dead-man |
| dispatch clamp to 1 | x | | x | | x | | API severity label throttles a pool that is expiring; crash-before-charge tickets eat the one slot invisibly |
| LPP freezes (336 h in 14 d) | x | x | x | x | x | | key caps and scope; sterile continues uncounted; supervisor blind to infra; resume fix #108 unmerged |
| alert cry-wolf | | | x | x | | | fires per event, escalation per probe only, several guards per incident |
| tonight's outage (0.1) | x | | x | | x | | expiry unread, poll 429 silently ages to deny, pages per symptom |
| capability denials blocking fixes | x | x | | x | | | grants derived from a static list, not from what the harness itself hands the worker |
| poisoned research index | x | | | | | x | report written without grounding into a trusted corpus |

The densest columns are RC-A (8 of 9 rows) and RC-B/RC-C (5 each). That is where §3 spends
its top ranks.

---

## 3. Prioritized fix plan

Score = (impact x recurrence) / effort.
- impact 1-5: 5 = stops all autonomous work, a Frank-facing product (LPP stream, messenger, vault) or corrupts memory; 1 = cosmetic.
- recurrence 1-5: 5 = continuous or 3+ times in 30 d; 1 = once, theoretical.
- effort: S=1 (design exists, under a day), M=2, L=3.

Status is live at 10:30Z. "staged" = code merged into `claude-foreman/ops` but not installed.
Where a row has no ticket of its own, the named ticket's detail already specifies it.

| rank | fix | I | R | E | score | root cause | motivated by | ticket | status |
|---|---|---|---|---|---|---|---|---|---|
| 0 | Frank re-logs Claude Max on Lumen (physical) | 5 | - | - | prereq | RC-E | §0.1 | wq#1693 | open, user lane |
| 1 | **Unstick the dispatcher fixes**: land PR #419 (1670) after 1671 releases the file or rebased onto it; re-triage 1671 and 1669 (`FALSE_COMPLETE` with no signature is a classifier gap, not a verdict); re-run 1672 after rank 5 | 5 | 5 | 1 | 25.0 | RC-A, RC-C | WALK §6-7, CLOSE F1 | wq#1670, 1671, 1669, 1672 | all blocked |
| 2 | **Install the three staged fixes** that §0.3 found not live: universal timer dead-man, lumen_state lifecycle + sibling closure, contradiction scan. Each via change-safety, each with a box-side verify | 5 | 4 | 1 | 20.0 | RC-A, RC-B | ATLAS F-13, MAP §4.3, STALE fix 2 | wq#1658, 1632, 1609 (backfill step of wq#1696) | staged |
| 3 | **Merge or reopen the unmerged LPP fixes**: PR #108 (resume ping-pong), PR #112 (:8871 owner), and re-adjudicate CLOSE's 13 | 5 | 4 | 1 | 20.0 | RC-B | CLOSE §2, LPP-ARCH F1-F2, LPP-BEH E1 | wq#1672 (re-adjudication), wq#1681 | PRs open |
| 4 | **Reconfirm-on-match**: reconcilers ingest equal observations so `last_confirmed_at` moves | 4 | 5 | 1 | 20.0 | RC-A | STALE §2.1, MAP §5 #1 | wq#1608 | open, p0 |
| 5 | **Grants match what the harness hands out**: worktree, `wq scratch`, `$TMPDIR`; denials on p0/p1 raise one aq row with the policy diff | 5 | 5 | 2 | 12.5 | RC-A, RC-D | 1672/1610/1616 block reasons, BROKER_GRANT_COVERAGE | wq#1697 | open |
| 6 | **Governor interim knob**: `SOFT_MARGIN_PCT` and `severity_throttles` read from `floors.json`; off when floors are zero | 4 | 4 | 1 | 16.0 | RC-E | WALK §5 | wq#1669 (interim clause) | blocked |
| 7 | **Deploy the guard exit-code contract** (finding vs crash) and tag the 10 guards | 3 | 5 | 1 | 15.0 | RC-C | ATLAS §5 #1, F-12 | wq#1679 (built as wq#1657) | staged |
| 8 | **Credential and budget registry with lead time**, and cause-attributed brain/dispatcher pages | 5 | 5 | 2 | 12.5 | RC-E, RC-A | §0.1, LPP-BEH E2/E6/E7, ATLAS F-8/F-9, CLOSE F1 | wq#1694 (instances wq#1646, 1647) | open |
| 9 | **LPP infra modes**: classify 401/403/budget as dead rungs, `AllRungsDeadError` -> `brain_down` + hold-down instead of flap, sterile-continue ledger, supervisor `BRAIN_DOWN`/`RESUME_FLAP` | 5 | 5 | 2 | 12.5 | RC-A, RC-C, RC-E | LPP-ARCH F3-F5, D2-D3; LPP-BEH §0 family 1 | wq#1660 | open |
| 10 | **Quarantine the fabricated reeval docs** from `research_docs` (bandaid) | 4 | 3 | 1 | 12.0 | RC-F | §0.3 | wq#1695 (step 0) | open |
| 11 | **Merge the two handoffs**; generated half rendered at `--core` | 3 | 4 | 1 | 12.0 | RC-D, RC-B | MAP §1.10, §5 #2 | wq#1630 | open |
| 12 | **Projection dirty flag** + `--core --if-dirty` regen + freshness WARN | 3 | 4 | 1 | 12.0 | RC-A, RC-B | MAP §4.4, §5 #5 | wq#1633 | open |
| 13 | **Stop supergateway body logging** to journald; check Bes logs for token-shaped strings | 4 | 3 | 1 | 12.0 | RC-C, RC-F | ATLAS §5 #10, §0.3 | wq#1698 | open |
| 14 | **Done requires a deploy receipt** for any PR that stages `ops/*/install.sh`; `--verify` mode + daily ops-deploy-drift sweep | 4 | 5 | 2 | 10.0 | RC-B | §0.3 | wq#1696 | open |
| 15 | **Conditions, not events**: aq escalation per source + spam suppression; recurring detectors write keyed aq rows, not tickets | 4 | 5 | 2 | 10.0 | RC-C | ATLAS §5 #2, CLOSE F5 | wq#1649, 1674 | open |
| 16 | **Audit row on `ingest` and CLI `commit`** | 3 | 3 | 1 | 9.0 | RC-F | MAP §4.5 | wq#1634 | open |
| 17 | **Inventory owns declared keys only**; observed keys projected from the store | 4 | 4 | 2 | 8.0 | RC-D | MAP §4.2, §5 #3 | wq#1631 | open |
| 18 | **Liveness on-ramp** mandatory probe tag + derivation + backfill | 3 | 4 | 2 | 6.0 | RC-A | CLOSE F2/F6 | wq#1676 | claimed, in flight |
| 19 | **Research grounding gate** in fleet_graph; no self-assigned proposal status | 4 | 3 | 2 | 6.0 | RC-F | §0.3 | wq#1695 | open |
| 20 | **Burn-rate governor controller** replacing the binary tight flag | 3 | 4 | 2 | 6.0 | RC-E | WALK §5 | wq#1669 | blocked |
| 21 | n8n-watcher topic typo (a silent black hole) | 3 | 2 | 1 | 6.0 | RC-A | ATLAS F-6 | wq#1645 | open |
| 22 | Guards over ghosts: split-watch, retirement-audit check D, rerank-thread-watch | 2 | 3 | 1 | 6.0 | RC-A, RC-B | ATLAS F-4/F-5/F-10 | wq#1643, 1644, 1656 | open |
| 23 | Workers stop re-running `--core`; fworker episodes to `archival_notes` | 3 | 3 | 2 | 4.5 | RC-D | MAP §1.17, §4.6 | wq#1636, 1635 | open |
| 24 | LPP perception budget: advisory priorities, `TurnFrame`, `STORY_ORDER` exhaustion warning, walk-interrupt reason, battle bag | 3 | 3 | 2 | 4.5 | RC-B | LPP-ARCH F6-F8, LPP-BEH family 2 | wq#1662, 1663 | open |
| 25 | Healer table gaps + subscribe-side round trip | 4 | 2 | 2 | 4.0 | RC-A | ATLAS §5 #5 | wq#1650 | open |
| 26 | Read-only subsystem locks | 2 | 4 | 2 | 4.0 | RC-D | CLOSE F7 | wq#1675 | open |
| 27 | Core prose migration; one embedder | 2 | 3 | 2 | 3.0 | RC-D | MAP §5 #11, #14 | wq#1637, 1639 | open |
| 28 | Single cloudflared actuator with cooldown | 5 | 1 | 2 | 2.5 | RC-D | ATLAS §5 #8 | wq#1652 | open |

Two ranking notes that the score alone hides:

- Rank 5 scores below ranks 6-7 but must run first in practice: it unblocks the P0 in rank 1
  (1672) and two P1s. Order of execution is 0 -> 5 -> 1 -> 2 -> 3 -> 4, then by score.
- Rank 28 scores last because it has not happened yet. Its impact is a Commander SIGKILL
  mid-change or a vault outage during failover. Do it before the next HA drill, regardless of rank.

---

## 4. If you fix only these five

Chosen by recurring friction removed, not by score. Each closes a root cause, not a symptom,
and each is mostly built already.

| # | fix | ranks | root cause closed | friction it removes |
|---|---|---|---|---|
| 1 | **Make the fix path work**: grants match harness-issued paths, then land/re-run the four blocked dispatcher and wq tickets | 5, 1 | meta M1 | all four dispatcher and wq-gate fixes filed by WALK and CLOSE (1669-1672) are blocked by the machinery they repair; nothing in §3 moves while that holds |
| 2 | **Done means live**: deploy receipt on `wq done`, then install the three staged fixes and merge or reopen LPP #108/#112 | 14, 2, 3 | RC-B | false-complete tickets (13 + 2 PRs + 3 staged bundles found); delivers a universal timer dead-man, lifecycle closure and the contradiction scan the same day |
| 3 | **Countdowns, not cliffs**: credential/budget registry with lead time, governor knob off when floors are zero, LPP `brain_down` hold-down | 8, 6, 9 | RC-E | tonight's outage; the clamp to 1; ~202 h of LPP brain-dead in 14 d; jay-watch and merge gate on token moves |
| 4 | **One row per condition**: guard exit contract deployed, aq escalation per source, detectors through aq, cause-attributed pages | 7, 15, 8 | RC-C | alert cry-wolf (250 rows, 9 duplicate repo-sweep tickets, 9,200 supervisor reloads, 6 pages for one logout); red `--failed` wallpaper |
| 5 | **Facts carry evidence and one owner**: reconfirm-on-match, inventory split, dirty-flag projections, one handoff | 4, 17, 12, 11 | RC-A (facts), RC-D | stale-fact drift, the class Frank catches most (thoth, nephthys, storage manifest, IPs in two authorities) |

What these five do not cover, on purpose: research grounding (rank 19; the bandaid at rank 10
contains the damage), LPP perception quality (rank 24; worth doing once the brain stays up),
and the embedder and prose migrations (rank 27; cost, not correctness).

---

## 5. Meta-defects in the orchestration itself

Why these problems kept reaching Frank by hand. Stated as defects of the system that runs
the work, not of any one lane.

**M1. The repair path runs through the broken component.** Tonight five audits filed
roughly 45 tickets. The ones that repair the dispatcher, `wq done` and capability policy
are dispatched by the dispatcher, closed by `wq done` and executed under the capability
policy. At 10:30Z: 1670 blocked on a same-file landing conflict with 1671, 1669 and 1671
blocked by a failure classifier that returns `UNKNOWN` instead of a class, 1672 denied its
own worktree, and 0 workers running because of §0.1. An audit that files a P0 against the
dispatcher has no route that avoids the dispatcher. Fix: a small, protected "repair lane"
whose tickets are dispatched by a separate minimal path (fixed model, fixed grants covering
`bin/`, `foreman/`, `minerva/` worktrees and scratch, no governor floor), restricted to
tickets that cite a failing guard or audit finding.

**M2. Done is judged in the lane repo, not on the box.** Lane -> one repo forces fixes for
`bin/`, `minerva/` and `/etc/systemd` into staged bundles, and the evidence gates check the
repo. `[verify]` tickets verify the staged code. Result: a fix can be designed, reviewed,
merged, verified and closed without ever running. (wq#1696, rank 14.)

**M3. Detection is plentiful; diagnosis happens only when a session goes looking.** Tonight's
outage was visible to at least five tools, each holding one fact: `claude auth status`, the
credentials file shape, the usaged journal, `can-dispatch`, the dispatcher skip histogram.
Six pages fired. None joined the facts. The cause was found by a synthesis ticket reading
state for context. Same shape in LPP-BEH (the supervisor prescribed grinding to a process
that could not make an HTTP call for six days) and ATLAS §6 (two guards failing every 5 min
over one branch, no ticket). Fix: every symptom page carries the dominant upstream reason it
can compute cheaply (rank 8), and aq_escalate groups rows that share a cause key before
filing (rank 15).

**M4. Every incident grows a new detector with its own voice.** 49 guard scripts, about 20 aq
writers, four ntfy topics (one misspelled), three notions of "stalled". Aggregation was
built once (aq_escalate) and scoped to probes. The human is the only component that reads
all of it, which makes Frank the de facto correlator. The instinct "Frank is not the
error-detection system" is correct and is currently not true.

**M5. Parallel burn creates collisions the queue cannot resolve.** The fast-burn window ran
many workers on overlapping files: 1670/1671 on `harness/fworker.py`, the `bin/` primary tree
left on a feature branch (wq#1653, the fourth such incident), the pokemon primary tree left
dirty (wq#1618), an analysis ticket refused because another read-only ticket held `foreman`
(CLOSE F7). Leases and slocks prevent double-claims; they do not prevent two tickets from
needing the same file. Fix: `--subsystems` gains file-scope declarations for build tickets,
and the router serializes tickets whose declared files intersect.

**M6. Unverified inputs are fed to synthesis by the orchestrator.** This ticket's own brief
named the `reeval_*` set as an input alongside audited docs. The set is mostly fabricated
(§0.3). The fleet research queue has no grounding step, its output goes to the same corpus
the proposal guard trusts, and nothing marks a report as unverified when it is handed to the
next agent. A synthesis that had trusted the brief would have ranked a $12k/month caching
saving on a $170/month budget. (wq#1695.)

**M7. Every session rediscovers.** Knowledge is spread across 22 stores and a ~32K-token boot
that still omits the live generated handoff (MAP §2.1). Tonight three audits independently
rediscovered the wq#1581 token-move fallout (ATLAS F-8 jay-watch, CLOSE F1 merge gate,
wq#1619 docs). A fact that has to be rediscovered is a fact that was not owned (RC-D).

**M8. The instruction plane outside the fleet holds fleet facts.** Frank's preferences block and
the Anthropic-side skills carry hostnames, IPs and node roles no fleet tool can regenerate
(MAP §1.14); when the fleet moves they drift and Frank repeats himself. wq#1640 renders that
block from the store and tickets only a diff.

**The short form.** Problems reached Frank manually because the fleet has many detectors and
no diagnoser, because "done" never touches the box, and because the path that would repair
the fleet runs through the parts that are broken. Ranks 1, 5, 8, 14 and 15 are the ones that
change that; everything else in §3 makes individual subsystems better.

---

## 6. Method and evidence

Read in full: CLOSE, WALK, MAP, LPP-ARCH, the seven REEVAL files. Read by section: ATLAS §0-1,
§3-7; LPP-BEH §0-1; STALE §0-1; COST §1. Counts quoted from those docs are as they measured them
(ATLAS and MAP at 06:3x-06:5x UTC) and were not re-measured unless marked live.

Live checks run for this doc (all read-only; values never printed for secrets):

```
wq show / sqlite3 -readonly queue.db  (tickets 1600-1698: status, block_reason, notes)
tail dispatcher.log | pass_end + skip-reason histogram of the last pass
python3 foreman/governor/foreman_usaged.py can-dispatch claude-sonnet-5
python3 -c json.load(foreman/state.json)  (stale, limits, severity)
journalctl -u lumen-job-foreman-usaged-*  (429 streak, CCC fallback)
PATH=/home/lumen/.npm-global/bin:$PATH claude auth status
python3 credentials shape check  (field lengths and expiry timestamps only)
curl api.github.com/repos/frankstupak/lumen-plays-pokemon/pulls/{108,109,111,112}
ls foreman/ops/wq*/install.sh ; ls bin/lumen-contradiction-scan ; ls /etc/systemd/system/*.service.d/99-cron-stamp.conf
grep -rIl memory_vectors|/opt/minerva|alerts.raw|mem_invalidator|lumen-promptcache|lumen_usage_agg  (0 refs each)
systemctl show lumen-research-index -p Environment ; psql research_docs where filename like 'reeval_%'
sqlite3 -readonly minerva.db archival_notes (last 14 h, titles)
```

Tickets filed by this synthesis: wq#1693 (Frank login), 1694 (credential lead time and cause
attribution), 1695 (research grounding), 1696 (done means live), 1697 (capability grants),
1698 (supergateway body logging). Root-cause notes added to wq#1685, 1686, 1672.

What this doc does not claim: that the scores are measurements (they are ordinal judgements
from the cited evidence); that the source docs' per-item designs are correct (each ticket
still gets its own design and test-to-failure); or that §0.1 is the only thing stopping
dispatch after Frank logs in (the 429 on `/api/oauth/usage` predates the logout and may
persist; wq#1694 item 3).
