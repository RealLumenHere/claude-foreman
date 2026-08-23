# A14 — The quantum-leap plan: one decision sheet from A1–A13

Ticket wq#764. Campaign spec: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md`.
Integration pass, 2026-08-23. **No new rules are invented here.** Every recommendation traces to
a numbered finding in A1–A13 or to a live measurement taken during this pass; where two reports
disagree, §1 names the disagreement and picks, with the reasoning, rather than averaging them.

**Nothing was changed by this ticket** outside `reports/`. §4's tickets are written and not seeded.

---

## 0. The answer, before the argument

Three things are true that the campaign spec did not know when it was written:

1. **Frank granted full autonomy on 2026-08-22** (`instincts.yaml:96-101`). That grant is wider
   than anything A6 proposed and it dissolves the premise of the campaign's own §2 R1. A6 was
   measured on 2026-08-22 and does not mention it; A7 caught it. **The approval question is
   closed. The evidence question is not.**
2. **The campaign has already moved its headline number.** A13 froze `time_parked_on_frank = 74`
   at cutoff 2026-08-21 18:06:42. The live gauge, read from Lumen's node_exporter at
   2026-08-23 06:20Z, reads **`lumen_queue_frank_parked_total 30`**, and `status=blocked` has gone
   **52 → 14**. The count fell by 59%. The *tail did not move*: `blocked_age_days{max_d} 42.1`.
   The items that remain are the ones A2 classified as irreducibly Frank's — bench work, physical
   hardware, a credential on his personal account, one purchase.
3. **The constraint the spec named third is now the only one left.** F1 (Frank as serialization
   point) is 59% drained. F2 (autonomy off in config) is partly fixed and fully diagnosed. F5, F6,
   F8 are closed with working infrastructure. **F3 — worker success rate — is untouched, and it is
   now the binding constraint.** A1 measured 30% on the highest-volume archetype and 44% of
   measured worker-hours producing nothing shippable. Every other lever in this plan multiplies
   that number; none of them changes it.

So the plan is ordered: **make the law consistent (nothing else is safe while agents read
contradictory instructions) → turn on the enforcement that already exists → fix the success rate →
then spend the headroom.** Raising concurrency before fixing success rate buys more failures at
14% of a weekly budget we are not using anyway.

**One dependency is missing and I will not paper over it: A12 produced no report.** See §5.

---

## 1. Where the reports disagree, and what I picked

Seven real conflicts. Each is stated as both sides, then a pick.

### C-A. A6's adoption ladder vs. Frank's autonomy grant

- **A6 §9** (the JUDGE step): adopt the Class S table but keep **every S row switched off** until
  the precondition runner, the supervisor-owned paired restore, and `approvals.yaml` exist. Its
  argument is strong: "prose preconditions have already failed twice in this fleet (fq#592, then
  fq#601 the same night). A third round of prose is not a design."
- **`instincts.yaml:96`, 2026-08-22**: *"Do not ask permission for reboots, installs, config
  changes, service restarts, spend within cap, hardware actions on fleet nodes, or anything
  previously gated."* And `:98`: *"NO NEW FRANK-GATES."*

**Pick: the grant governs; A6's machinery survives, A6's permission table does not.**

Reasoning. A6's ladder asks Frank to ratify a narrower permission set than the one he already
granted. Building `approvals.yaml` and switching six rows on one at a time would re-create, as a
data structure, precisely the gate `instincts.yaml:98` forbids — and an agent cannot re-gate what
Frank ungated. But A6's *evidence* case is independent of its *permission* case and survives
intact: P0 (no conclusion in the same batch as its check), P1 (restore-proven backup for this
target in this run), P3 (rollback exercised this run), P4 (paired restore registered with the
supervisor), P5 (planted-positive test of any cap in the path) are sourced from seven real
incidents and none of them is about who said yes. Under full autonomy they get *more* important,
not less, because the halt that used to be an accidental brake is gone.

**Consequence for the plan:** drop `approvals.yaml` and the S-row ladder (§6). Build P0–P5 as the
precondition runner (§3, Phase 6), scoped to destructive actions regardless of authorization. A6's
§6 G4 rewrite is still the right replacement text — with its "Class S / Class A" framing replaced
by "evidence required / evidence not obtainable → don't do it", which is what the grant leaves.

### C-B. A7 "the maintenance window is now unnecessary" vs. A11 "the window is the delivery vehicle"

- **A7 R4**: the reboot rule is retired, therefore *"A maintenance window is now unnecessary: the
  rule it worked around is gone."*
- **A11 §0**: the fleet does not have a patching problem, it has a **patch-visibility** problem
  and an N-mutations-per-approval problem. Security apt already lands unattended (`apt_security=0`
  on all 13 reachable nodes). What does not land is what `unattended-upgrades` deliberately will
  not touch: application and container versions, firmware, the non-security apt tail, tailscale
  drift on two nodes, and the reboots that make kernel updates real.

**Pick: A11.** A7 is right about the *premise* it addresses (the reboot rule) and wrong about the
conclusion, because it reasoned from the rule rather than from the drift. A11 measured the drift.
A window is not a safety rule needing a retired premise — it is a batching mechanism for work that
is inherently N mutations with N independent verifications. Both reports agree the campaign's CVE
evidence (wq#383) is void; that agreement does not make the window unnecessary.

### C-C. A7 "flip `--universal` default-on" vs. A8 "keep the flag, add callers"

- **A7 §6 step 2**: *"`--universal` default-on + one caller. Smallest diff in this report with the
  largest enforcement gain."*
- **A8**: fixed the seven REQUIRE violations and the one AGREE disagreement, added
  `dispatch-lint --universal` as a **preflight in `wq-lane-init`** (tested on both the pass and
  refuse-and-clean-up paths), and **staged** the timer unit with `--universal` because
  `/etc/systemd/system` is root-owned.

**Pick: A8's ordering, then A7's flip.** Verified live this session: `/home/lumen/bin/dispatch-lint`
still gates the check behind `--universal` (line 199), and the installed unit's `ExecStart` is
still bare `/home/lumen/bin/dispatch-lint` (line 18) — so A7's enforcement gap is **still open**
and A8's staged fix is **still uninstalled**. Get continuous enforcement running first with no
semantics change, let it run clean for a week, then flip the default. Flipping the default while
the only scheduled caller doesn't pass the flag changes behaviour for every ad-hoc caller and
still leaves the timer silent — the worst of both.

**But neither report noticed the cheaper path.** A8 staged a *root* unit because the existing timer
is root-owned. A3 solved the identical problem by deploying node_exporter fleet-wide as
**unprivileged `systemd --user` units** — 14/14 hosts, no root, no sudo. The same move works here:
a `--user` timer needs no Frank action at all. §3 Phase 2 takes that path and keeps the staged root
unit as the fallback.

### C-D. A5 "raise cloud_ceiling to 5" vs. A1 "44% of worker-hours buy nothing"

- **A5 §4**: PSI shows zero CPU starvation, zero memory pressure and zero reclaim stalls at load
  average 22 on 44 HT cores with 48 GB available. Ceiling 5 is defensible; 7 is feasible on paper
  but not without PSI monitoring.
- **A1 §5**: 268 attempts consumed 25.0 worker-hours; only 14.0h produced a SUCCESS.

**Pick: raise to 5, but only with A5's PSI trigger landing in the same wave.** These do not
actually conflict — A5 measures whether the box can take it, A1 measures whether it is worth it —
but the composition matters and neither report states it. At A1's 30% `standard-engineering`
success rate, two more concurrent slots buy ~0.6 more successes and ~1.4 more failures per pass.
That is still net positive at 14% weekly budget utilisation (spec F4), so raise it; it is *not*
positive enough to justify going to 7, and it is not a substitute for Phase 4.

Verified live: `cloud_ceiling: 3`, `max_spawns_per_pass: 3`, and `foreman_dispatcher.py`'s only
`tight_mode` trigger is `governor_pressure` — budget, not box. A5's PSI trigger does not exist.

### C-E. A7 "R5 keep — blocked on measurement" vs. A5 "R5 measured, keep"

- **A7 R5**: *"KEEP FOR NOW — BLOCKED ON MEASUREMENT. This is the one named rule I decline to
  recommend changing."* Its real reason for keeping: Lumen is the only host with the G0 host guard
  (`[ "$(hostname)" = lumen ] || exit 9`) baked into every playbook.
- **A5 §7**: measured. *"'All builds run on Lumen' is still affordable... The rule is not hurting;
  the config is just set too low."*

**Pick: both, composed. The block is cleared and the verdict is unchanged.** A5 supplied the
measurement A7 was waiting for (and A3 supplied the fleet-wide series that makes it re-checkable),
and the answer came back "keep". So the only action left is A7's interim proposal: change the
rule's *stated reason* in `session-start.md` from implied capacity to the real one — the G0 host
guard — so the next reader knows what it protects. That is a one-line edit, not a policy change.
No ticket needs to wait on this.

### C-F. The campaign spec's own evidence vs. A2, A7 and A11

Three reports independently verified that **wq#383 closed 2026-08-01**, twenty days before the
audit cited it as "33 days of known-CVE exposure". F1's flagship example and R4's headline cost
argument are both void. A2 additionally found that 8 of the 44 `blocked` tickets were worker
failures wearing a `blocked` badge (belonging to A1, not F1), and that wq#653's premise died the
day it was filed.

**Pick: A2's numbers replace F1's throughout.** 77 items, of which 30 genuinely Frank's, 9
agent-answerable, 10 stale, 28 never should have been gated. Do not carry the CVE argument into
any downstream document; it will not survive review. A11 §9 already tickets the `HANDOFF.md`
correction — keep it.

### C-G. A13's attribution vs. everything landing at once

A13 predicts A9's evidence gate makes `rework_rate` **rise** short-term (invisible FALSE_COMPLETEs
start getting caught) and A1's ceilings make worker success rise. Those land in overlapping waves,
and A13's own rework metric was retracted mid-campaign (wq#841 erratum — the 1.74% baseline named a
marker that erased itself; the live gauge now reads `lumen_queue_rework_rate_pct 0.47` on a
structural series that only starts 2026-08-23).

**Pick: one dated re-baseline per wave, not one at the end.** A13's freeze discipline
(`opsaudit_a13_baseline_20260821.json`, sha-recorded, never edited in place) is right; the
mistake would be a single "after" snapshot attributed to thirteen simultaneous changes. §3 puts a
re-baseline at the end of each phase, and §6 refuses to predict where A13 said "no prediction".

**Also note the two success-rate numbers are not the same measurement.** A13's frozen baseline says
22% attempt-level success over 149 attempts; A1's report says 30% for `standard-engineering` over
268 attempts. A13 froze at cutoff; A1's dataset kept growing and is recomputed on every run
(wq#839). Compare like with like: use A13's frozen 22% for before/after, and A1's live recompute
for tuning.

---

## 2. Section 1 — ALREADY DONE

Verified in this session where a live check was possible. "Live" means I confirmed the artifact is
running now, not that a report says it shipped.

| # | What | Evidence | Verified how |
|---|---|---|---|
| D1 | **F6 closed.** node_exporter on 14/14 reachable hosts, unprivileged `--user` units, feeding the existing VictoriaMetrics on Bes. VM metric names 37 → 458 | A3 (wq#753) | **Live** — `curl 100.117.66.102:9100/metrics` returns `amdgpu_vram_total_bytes` + full node series |
| D2 | **F5 closed.** Langfuse project `foreman` created, keys at `LANGFUSE_ENV`, dispatcher traces + per-attempt fworker spans (`fworker-wq{id}`, span `attempt-{n}:{classification}`) | A4 (wq#754) | Report: 4 real traces confirmed via `/api/public/traces`. Not re-verified here (`.secrets` is guard-denied by design) |
| D3 | **F8 closed.** `wq done` refuses to close without a URL / sha / existing path, or explicit `--no-artifact "<reason>"` on the record. `wq closure-quality` reports the trend per lane per week | A9 (wq#759) | **Live** — `--no-artifact` present in `/home/lumen/bin/wq` |
| D4 | **A13's flow metrics live**, exported through A3's pipeline | A13 (wq#763) | **Live** — 28 `lumen_queue_*` series on Lumen's node_exporter |
| D5 | **F7 substantively closed.** All 7 REQUIRE violations + the heartbeat-TTL disagreement fixed in the six dispatch files; TTL resolved to 240 (what `wq` enforces); `wq-lane-init` now refuses to hand out a non-compliant lane | A8 (wq#758) | Report; both branches tested. **Enforcement timer NOT installed** — see §3 Phase 2 |
| D6 | **F1 59% drained.** `time_parked_on_frank` 74 → 30; `blocked` 52 → 14; A2's moot/un-gate classes partially applied (`653`, `693`, `328`, `420`, `702` all now `done`) | A2 (wq#752) + live | **Live** — `lumen_queue_frank_parked_total 30`, `lumen_queue_status_total{status="blocked"} 14` |
| D7 | **F2 diagnosed and half-fixed.** `build` lane enabled; 8 of 9 config lanes enabled; root cause found (`wq-lane-init` never registers a lane in `config.json`, and nothing lints the drift) | A10 (wq#760) | **Live** — config read: only `user` is `enabled:false`, correctly |
| D8 | **Turn ceilings lifted as a stopgap**, all archetypes at 600, with the reasoning stamped in place pending real assistant-turn accounting | A1 + `fworker.py:109-124` | **Live** — read from source |
| D9 | **The drift probe is built but unmerged.** `dispatch_config_lint.py` (FAIL: open-queue lane missing from config; WARN: config lane with 0 tickets), wired into `lumen-foreman-health.service/.timer`, **ships disabled** | wq#845 | PR #92 open; file not present on `main` |

**Two things called "done" are done-in-a-PR, not done-on-disk:** D9 (PR #92) and A13's exporter
deploy script (PR #53) / A3's (PR #52) / A9's (PR #86). The *infrastructure* from A3, A9 and A13 is
live — I checked — but the repo record trails it. That gap is itself an instance of F8's family:
the artifact exists and the system of record does not know. Merging those PRs is Phase 0.

---

## 3. Section 2 — AGENT-EXECUTABLE NOW, IN ORDER

Ordered so each phase is verifiable before the next starts, and so nothing waits on a judgment
Frank has not already made. Ticket text for every line is in §4.

### Phase 0 — Land what is already built (blocks nothing, unblocks the record)

Merge the four open ops-audit PRs (#52 A3, #53 A13, #86 A9, #92 A10/wq#845) and enable
`dispatch_config_lint.py`, which currently ships disabled. **Predicted movement:** none on any flow
metric — this is bookkeeping. **Rollback:** revert the merge; the live infrastructure is
independent of the repo state, which is the whole problem being fixed.

### Phase 1 — Make the law consistent (T1–T5)

**Why first:** every phase below tells an agent to act without asking, while six live files still
tell it to halt. A7 measured twelve contradictions; C1 is `instincts.yaml:17` versus
`instincts.yaml:97`, **80 lines apart in the same file** — verified still present this session. An
agent loading the corpus today gets both. This is Frank's own `REPEAT = FLEET DEFECT` rule applied
to the autonomy grant itself: the grant did step 1 (say it) and skipped step 2 (fix every copy).

- **T1** Sweep the retired reboot/approval rules from all six stale locations (`change-safety.md`
  G4 + HARD RULES, `session-start.md:40-41`, `UNIFIED_DISPATCH.md:96-101`, `manifest.yaml:35`,
  `instincts.yaml:17`). Thor carve-out survives **verbatim**; `manifest.yaml` becomes
  `reboot_thor: red`, `reboot: green`. Add A7 §4.8's two RETIRE lines to `RETIRED_RULES.md` so
  `dispatch-lint` catches any recurrence.
- **T2** Reword R2 across `change-safety.md:81`, `manifest.yaml:41`
  (`anthropic_api` → `anthropic_token_billing`, still `red`) and `session-start.md:41`, using A7's
  drop-in text. Correct `instincts.yaml:51`, which is factually false about the fleet's own
  architecture — the worker binary *is* Claude Code.
- **T3** One-word fixes: `instincts.yaml:26` `search` → `recall` (the documented command does not
  exist); strike the drained persona-chain headline from `UNIFIED_DISPATCH.md:14-22`.
- **T4** Replace G4 with A6 §6's text, reframed per §1 C-A: evidence, not permission. No
  `approvals.yaml`, no S-rows.
- **T5** Restate R5's reason in `session-start.md` as the G0 host guard (§1 C-E). One line.

**Predicted movement:** no flow metric. This is a correctness fix; A13 rightly declines to predict
on it. **Verification:** `dispatch-lint` goes red on the new RETIRE lines, you fix, it goes clean.
**What could go wrong:** deleting a copy that some agent path reads exclusively. **Rollback:**
every file is under `instructions/` with `.bak` convention; the sweep is text-only and reversible
by `git checkout` / `.bak` restore, and no runtime reads change.

### Phase 2 — Turn on enforcement that already exists (T6)

- **T6** Install `dispatch-lint --universal` on a schedule. **Take the `--user` timer path** (§1
  C-C): a `systemd --user` timer owned by `lumen`, following A3's precedent, needs no root and no
  Frank. Keep `/home/lumen/bin/lumen-dispatch-lint.service.staged` as the fallback if the `--user`
  path is rejected on review. Do **not** flip `--universal` to default-on yet — after one clean
  week, that becomes a two-line follow-up.

**Predicted movement:** none on flow metrics (A13: "a drift-prevention control, not a throughput
one"). **What could go wrong:** the timer fires on a legitimately-divergent per-lane rule and
trains everyone to ignore it — the exact failure A8's original opt-in comment was avoiding. That
risk is now low: A8 fixed all seven violations, so the timer starts green. **Rollback:**
`systemctl --user disable --now`.

### Phase 3 — Spend the headroom, with its brake (T7–T9)

- **T7** PSI-based `tight_mode` trigger in the dispatcher, per A5 §5: `memory some avg60 > 5%` →
  tight mode; `> 15%` → hard stop; `memory full avg60 > 0%` → hard stop + ntfy. Today's only
  trigger is budget pressure, which does not know the box exists.
- **T8** `cloud_ceiling` 3 → 5. **Gated on T7 landing**, not on Frank.
- **T9** Stop and disable `lumen-rerank.service` — 329 MB RSS, 0 call sites (confirmed wq#569, and
  the unit is `active` right now). Note this is a `systemctl stop|disable`, which `guard.sh:88`
  denies to worker sessions by design and which A6 deliberately excluded from every standing class
  (fq#45: an unpaired stop cost 62 hours). Execute it from a Commander/interactive path with the
  paired re-enable command written down first, not from an fworker.

**Predicted movement:** A5 predicts no contention change (there is none to relieve). The honest
prediction is on **throughput**: `mean_per_week_overall 74.9` at baseline; +2 concurrent slots at
A1's measured 30% success should raise weekly completions, and will raise failures faster than
successes. **Falsified if** weekly completions do not rise at all over two weeks at ceiling 5.
**What could go wrong:** five concurrent workers plus LPP plus ClickHouse pushes swap pressure —
which is precisely what T7 exists to catch, which is why T8 waits for it. **Rollback:** set
`cloud_ceiling` back to 3 (single JSON field, takes effect next 20-minute pass).

### Phase 4 — Fix the actual constraint (T10–T12)

This is the phase that matters. Everything above multiplies the success rate; only this changes it.

- **T10** Real assistant-turn accounting in the harness — count assistant turns, not hook frames.
  A1 §2 measured that fworker's `TURN_BACKSTOP_BUFFER=5` supervisor kill fired on **66 attempts, at
  exactly `max_turns+5` every single time**, while the CLI's own counter overshoots its requested
  ceiling by up to 27% even when it self-reports cleanly. Until this lands, every ceiling number is
  measured against a miscount. `fworker.py:110-113` already names this as the blocker.
- **T11** Set `ARCHETYPE_MAX_TURNS` to A1 §6's table (`standard-engineering` 370,
  `novel-design` 480, `verify-pass` 200, `rca-investigation` 210, `research-synthesis` 190,
  `probe-build` 240, `mechanical-edit` 70; `research` stays ≥40 pending a first success).
  **Gated on T10.** Sized off measured SUCCESS `turns_used` with headroom for the drift T10 fixes —
  not off today's placeholder 600.
- **T12** Escalate attempt 2's ceiling ×1.5 over attempt 1 when attempt 1 failed via
  TURNS or KILLED/turns_exceeded. A1 §4: **76/76 (100%)** of within-dispatch retries reused the
  identical ceiling, and **3/76 (4%)** recovered under it — while 36/44 (82%) of later successes
  across all attempts ran under a *different* ceiling. Retrying is not the lever; re-sizing is.

**Predicted movement (A1's own falsifiable predictions, adopted verbatim):** attempt-level success
rises above A13's frozen 22%; `standard-engineering` FALSE_COMPLETE falls below 23% but **only
weakly** — A1 measured the KILLED-then-FALSE_COMPLETE mechanism on just 6 of 22 FALSE_COMPLETE
tickets, so ceilings can only reach that minority. Escalated attempt-2 success should exceed 4%.
**Falsified if** the next 20 escalated retries succeed at ≤4%, or `standard-engineering`
FALSE_COMPLETE is unchanged after 20 attempts at the new ceilings.
**What could go wrong:** T11 lowers ceilings from 600 to 370 and truncates a population A1's data
did not capture (its largest observed `standard-engineering` success was 286 turns, n=38).
**Rollback:** `ARCHETYPE_MAX_TURNS` is a dict literal in `fworker.py`; restore 600 and re-dispatch.
Do T11 **after** T10 so the rollback signal is trustworthy.

### Phase 5 — Lanes and the window (T13–T17)

- **T13** Patch `wq-lane-init` to register every new lane in `foreman/dispatcher/config.json` as
  `enabled: false` with the enable command printed — A10's root cause, the one that made 27 build
  tickets invisible for the entire campaign.
- **T14** Enable `fleet` (7 open), `memory` (3), `pokemon` (8); fold `fleet-spend` into `fleet` and
  `ops` into `build`; leave `research` and `quest-repair-research` disabled (A10 §4c). Set
  `fleet-fires.requires_approval` back to `true` so the config stops disagreeing with the code that
  overrides it.
- **T15** Build A11's `fleet-maint-assemble` (T-24h, unattended, completeness gate that exits
  non-zero, per-node advisory classification, batch JSON).
- **T16** Build A11's `fleet-maint-run` (ordered rolling execution, G0–G7 per target,
  stop-on-first-failure, auto-rollback, phase-3b recording that always runs). **This is A6's Class
  B done properly: one approval, N independent verifications — the approval batches, the
  verification never does.**
- **T17** Fix `fleet-update-check`: read the GitHub token, never render a question mark as data,
  fix the llama.cpp tag parser. A11 found this assembler bug **firing today** — a silent-failure
  path that reports "no updates" when it means "I could not check".

**Predicted movement:** T14 moves `throughput` in the newly-enabled lanes from ~0 dispatcher-driven
completions to nonzero, and drains 18 tickets of previously-invisible WIP (live:
`lumen_queue_wip_total{lane="fleet"} 7`). T15–T17 move nothing on the flow metrics — they move
patch latency, which A13 does not measure. Say that plainly rather than inventing a number.
**What could go wrong:** T14 enables 18 tickets at a 30% success rate, generating churn — argues
for doing Phase 4 first, which the ordering does. **Rollback:** each lane is one JSON boolean.

### Phase 6 — The evidence machinery (T18–T20)

A6's P0–P5, reframed per §1 C-A as **safety, not permission**. These now guard actions nobody has
to approve, which is exactly when they matter most.

- **T18** Precondition runner as a **separate process whose non-zero exit aborts the run** —
  P0 (a conclusion may never share a batch with the check that establishes it: fq#592, then fq#601
  the same night), P1 (restore-proven for this target in this run, to the wq#566 standard: restore,
  hash-match, and load it if it is loadable — fq#22 had `/home/lumen/pokemon` in **0 of 393**
  snapshots while backups ran green), P3 (a rollback exercised this run; dry-run counts, untested
  does not — fq#23).
- **T19** Supervisor-owned paired restore in fworker (P4) — registered before G5, cleared on a
  clean G7, executed by the supervisor on session death or watch-window failure. A6 calls this "the
  single largest build dependency in this proposal", and fq#614 is why: headless workers die
  silently mid-ticket, so the acting agent may not be alive to roll back.
- **T20** Planted-positive tests for every cap and alarm in the path (P5) — fq#10's
  `DAILY_POST_CAP=1` was present in code and ineffective; 25 posts shipped against an explicit
  repeated STOP. A cap nobody has watched trip is a comment.

**Predicted movement:** none on throughput. This is the control that makes Phase 3 and the autonomy
grant survivable. A13 would call it "no prediction" and it should be measured on incident count,
not flow. **What could go wrong:** a precondition runner that is itself unverified reproduces
fq#592. **Mitigation is T20 and it is not optional** — the runner ships with its own planted
positive or it does not ship.

### Phase 7 — Prove it moved (T21)

- **T21** Re-baseline after each phase into a **new dated artifact** — never edit
  `opsaudit_a13_baseline_20260821.json`, whose sha is already cited by other reports. Compare
  A13's frozen 22% attempt-level success against a like-for-like recompute, and watch
  `lumen_queue_reopened_total` (the structural series, not the retracted 1.74% marker) rise after
  A9's gate takes hold, then flatten within a month.

---

## 4. The tickets — written, ready, NOT seeded

Companion script: **`reports/opsaudit_a14_seed_tickets.sh`** — dry-run by default, prints every
`wq add` it would run and creates nothing without `--apply`. Per-phase flags so the plan can be
seeded a phase at a time. Same shape as `opsaudit_a2_apply.sh`.

| T | pri | lane | archetype | title | deps |
|---|---:|---|---|---|---|
| T1 | 1 | build | mechanical-edit | [ops-audit] Sweep retired reboot/approval rules from all 6 stale copies | — |
| T2 | 1 | build | mechanical-edit | [ops-audit] Reword R2 to per-token-billing across 3 files + manifest rename | — |
| T3 | 2 | build | mechanical-edit | [ops-audit] instincts `search`→`recall`; strike drained persona headline | — |
| T4 | 1 | build | novel-design | [ops-audit] Replace G4 with evidence-not-permission text (A6 §6, no approvals.yaml) | T1 |
| T5 | 3 | build | mechanical-edit | [ops-audit] Restate R5's reason as the G0 host guard | — |
| T6 | 1 | build | standard-engineering | [ops-audit] Schedule `dispatch-lint --universal` as a `--user` timer (no root) | — |
| T7 | 1 | build | standard-engineering | [ops-audit] PSI-based tight_mode trigger in the dispatcher | — |
| T8 | 2 | build | mechanical-edit | [ops-audit] Raise cloud_ceiling 3→5 | T7 |
| T9 | 2 | fleet | standard-engineering | [ops-audit] Stop+disable unused lumen-rerank.service (329 MB, 0 callers) | — |
| T10 | 1 | build | rca-investigation | [ops-audit] Count real assistant turns, not hook frames | — |
| T11 | 1 | build | mechanical-edit | [ops-audit] Set ARCHETYPE_MAX_TURNS per A1's measured table | T10 |
| T12 | 1 | build | standard-engineering | [ops-audit] Escalate attempt-2 ceiling ×1.5 after TURNS/KILLED | T10 |
| T13 | 1 | build | standard-engineering | [ops-audit] wq-lane-init registers new lanes in dispatcher config | — |
| T14 | 2 | build | mechanical-edit | [ops-audit] Enable fleet/memory/pokemon lanes; fold fleet-spend, ops | T13 |
| T15 | 2 | fleet | standard-engineering | [ops-audit] Build fleet-maint-assemble (A11 phase 1) | — |
| T16 | 2 | fleet | standard-engineering | [ops-audit] Build fleet-maint-run (A11 phase 3, A6 Class B) | T15 |
| T17 | 2 | fleet | standard-engineering | [ops-audit] Fix fleet-update-check silent-failure bug | — |
| T18 | 1 | build | novel-design | [ops-audit] Precondition runner as a separate process (A6 P0/P1/P3) | — |
| T19 | 1 | build | standard-engineering | [ops-audit] Supervisor-owned paired restore in fworker (A6 P4) | T18 |
| T20 | 1 | build | standard-engineering | [ops-audit] Planted-positive tests for every cap and alarm (A6 P5) | T18 |
| T21 | 2 | build | research-synthesis | [ops-audit] Per-phase dated re-baselines against A13's frozen artifact | — |
| T22 | 2 | build | research-synthesis | [ops-audit] Re-run A12 (external practice) — the missing dependency | — |
| T23 | 3 | build | mechanical-edit | [ops-audit] Merge PRs 52/53/86/92 and enable dispatch_config_lint | — |
| T24 | 3 | fleet | mechanical-edit | [ops-audit] Pin container image digests for the `:latest` set | — |
| T25 | 3 | build | mechanical-edit | [ops-audit] Correct HANDOFF.md §3 item 1 — wq#383 closed 2026-08-01 | — |

T22 is not optional bookkeeping — see §5.

---

## 5. Section 3 — NEEDS FRANK

**This section is deliberately short, and that is the campaign's result.** Under
`instincts.yaml:96-98` the only legitimate Frank dependency is *"a PHYSICAL action nobody else can
perform (touch hardware, mint a credential on his personal account, buy something)."* Applying that
test to A2's 30 "genuinely Frank's" items dissolves most of them: **wq#418** (reboot 12 nodes for
staged kernel/libc), **wq#464** (the W33 update pass), **wq#305** (fleet password rotation, staged
and one command from executing) and **wq#556** (back up `/home/lumen/pokemon`) are all now
agent-executable — and the right vehicle for the first three is A11's window (T15/T16), where each
target gets its own verification instead of an ad-hoc run.

What genuinely remains, each a yes/no with a recommended default:

| # | ask | why only Frank | rec |
|---|---|---|:--:|
| **F-1** | **Mint a classic PAT on RealLumenHere and accept invite 328977328** (wq#526, 8d) — fine-grained provably cannot work, verified 404/empty | Credential on his personal account. A2: "literally the one step no agent can do" | **Y** |
| **F-2** | **Buy the GL.iNet Comet GL-RM1 IP-KVM + ATX board, ~$116** (wq#391, 33d) | Purchase | **Y** |
| **F-3** | **Bench day** — hathor power-up/root-cause (#362), boot hapi-1 blade (#120), bench-validate the 24-port switch (#350), Nest UART recon (#11), per-Mac burn-in + auto-boot verify (#354/#337), plug rewire (#360) | Physical presence. These are the 42-day tail the campaign did **not** move | **Y, one day** |
| **F-4** | **Set the first maintenance window: 2026-09-12, 20:00 PT, ~6 minutes attended** (A11 §6/§7) | Calendar + his presence for node 1 | **Y** |
| **F-5** | **Reload the installed PWA once on your phone** (wq#12, 47d) — finishes the #18 messaging cutover; **and resubscribe to the renamed ntfy topics before the dual-publish window closes** (#339) | His phone | **Y** |
| **F-6** | **Bring Thor online** (wq#664) — Tailscale-offline all session | Thor is the one hard rule; nobody else touches it | **Y** |
| **F-7** | **pxpipe canary — route Claude traffic through a third-party proxy via `ANTHROPIC_BASE_URL`** (wq#634) | Risk acceptance, not a technical call: OAuth passthrough to a third party, and confabulation on exact strings is unmeasured on Sonnet-5 while our workers do git ops on hashes | **N** |
| **F-8** | **Firmware: Anubis has 23 pending `fwupd` updates** — bench session, not a scripted rolling run | Rollback is frequently impossible; a bad flash is a physical trip (A11 §6) | **Y, at the bench** |

Everything else in A2's sheet either already drained (D6) or became agent-executable when the grant
landed. **F-3 is the highest-value line on this page**: `blocked_age_days{max_d}` is 42.1 and
essentially all of it is bench work.

### The missing dependency, stated loudly

**A12 (CONSULT external practice — what a 15-node agent fleet should have that we do not) produced
no report.** Ticket wq#762 is still `claimed` with notes reading `INFRA_CRASH: KILLED after 2
attempts`. I searched every branch in the repo; no `opsaudit_a12_*` artifact exists anywhere.

Consequence, stated rather than papered over: **every recommendation in this plan is internally
derived.** A1–A13 measured this fleet against itself. Nothing here has been checked against how
anyone else runs a 15-node agent fleet, so the plan is well-evidenced about *whether our things
work* and silent about *whether we are missing a category of thing entirely*. That is exactly the
blind spot A12 existed to cover. T22 re-runs it; until it lands, treat §3's completeness — not its
correctness — as unverified.

**A bookkeeping defect worth naming while I am here:** A11's report *does* exist, committed on
`docs/wq761-maintenance-window`, while wq#761 is also marked `INFRA_CRASH ... KILLED`. A ticket
whose work landed is recorded as a crash. That is F8's failure class pointing the other way — the
artifact exists and the system of record does not know — and it is the same gap as the four
unmerged PRs in §2. Reconciling ticket state against landed branches deserves to be a standing
check, not a thing A14 noticed by accident.

---

## 6. Section 4 — DELIBERATELY NOT DOING

| # | Not doing | Why |
|---|---|---|
| N1 | **A second general observability platform** (SigNoz, OpenObserve, Beszel) | Spec §3.6, reaffirmed. VM+Grafana exist and are healthy; A3 and A13 just proved the existing stack absorbs new series. Adding a parallel stack repeats the F5 pattern of installing something and never wiring it |
| N2 | **`approvals.yaml` + A6's Class S ladder** | §1 C-A. Frank's 2026-08-22 grant is wider than the table; building it re-creates as data the gate `instincts.yaml:98` forbids. A6's P0–P5 survive as T18–T20 — the evidence half, not the permission half |
| N3 | **A weekly maintenance window** | A11 §6: measured drift is ~one minor version per component per 6–10 weeks. A weekly window is a weekly interruption to move one patch version, "which is how windows get skipped and then abandoned" |
| N4 | **`fwupd` in the rolling window** | A11 §6. Firmware rollback is frequently impossible. It goes to F-8, at the bench |
| N5 | **Flipping `--universal` default-on now** | §1 C-C. Get the scheduled caller running first; flip after a clean week. Two-line follow-up, not a phase |
| N6 | **Generating the six dispatch files from a shared substrate** | wq#561 and A7 R7 independently reject it: the files diverged past where a shared substrate exists, and per-lane divergence is often legitimate (POKEMON's `lpp-restart` scoping must not be universalised). REQUIRE/AGREE constrains without generating — that is the right design |
| N7 | **`cloud_ceiling` 7** | A5 §4: feasible on paper, enters the zone where build + LPP + rerank + ClickHouse cache could cause swap pressure. Revisit only after T7's PSI trigger has fired at least once in anger |
| N8 | **Retiring R5 ("all builds run on Lumen")** | §1 C-E. A5 measured it and the answer was keep. Only its stated reason changes (T5) |
| N9 | **Rewriting `notes` on A9's 6 recoverable note-less tickets** | A9 chose not to and was right: back-filling history makes `closure-quality`'s trend lie about when evidence appeared. The audit table is the record |
| N10 | **A minimum-ticket-detail gate at `wq add`** | Tempting — A1 §3 measured final-FALSE_COMPLETE tickets at a 693-char median versus 1245 for SUCCESS. But that is a correlation on n=17, A1 did **not** recommend it, and this ticket's constraint is integration, not a fourteenth opinion. Re-open if the correlation survives the Phase 4 re-baseline |
| N11 | **Extending autonomy to the Commander path** | A6 §7: wq#229's tier-1 blocklist and its `sudo`-strip patch are still staged and unapplied, and its own §7 gaps are unsolved. The worker path has `guard.sh`; Commander does not. Keep the asymmetry until the wall is under both |
| N12 | **Claiming the campaign made the fleet faster** | §1 C-G. Thirteen changes landing in overlapping waves against one "after" snapshot is unattributable. Per-phase dated re-baselines (T21) or no claim |

---

## 7. What this plan does not fix

- **It does not fix the largest failure classes.** A6 measured F9 SILENT_GAP + F1
  UNVERIFIED_ASSERTION at 29 of 66 curated incidents (44%). Phase 6 is the closest thing here to a
  control for F1; nothing in this plan is a control for F9. Removing a control that never addressed
  them is not the same as adding one that does.
- **It does not close the 42-day tail.** F-3 does, and only Frank can.
- **It has no external reference frame.** §5, A12.
- **It cannot attribute.** The campaign's own baseline (A13) predates a queue that has since moved
  59% on the metric that matters most, driven by A2's script *and* by ordinary work completing.
  Separating those is not possible from `queue.db` alone, which has no status-transition log
  (A13 §2, stated as a known limit).
- **Two of A1's three predictions cannot be tested for weeks.** They need 20 more attempts each.
  That is the correct pace and it should not be shortened by declaring victory early.

---

## 8. Sources

`reports/opsaudit_a1_worker_success.md` (wq#751) · `opsaudit_a2_frank_backlog.md` +
`opsaudit_a2_apply.sh` (wq#752) · `opsaudit_a3_node_exporter.md` (wq#753, branch
`feat/wq753-…-a3`) · `opsaudit_a4_langfuse.md` (wq#754) · `opsaudit_a5_lumen_contention.md`
(wq#755) · `opsaudit_a6_approval_model.md` (wq#756) · `opsaudit_a7_rule_corpus.md` (wq#757) ·
`opsaudit_a8_dispatch_lint.md` (wq#758) · `opsaudit_a9_evidence_gate.md` (wq#759, branch
`feat/wq759-…-a3`) · `opsaudit_a10_lane_defaults.md` (wq#760) ·
`opsaudit_a11_maintenance_window.md` (wq#761, branch `docs/wq761-maintenance-window`) ·
**A12 (wq#762) — no artifact, see §5** · `opsaudit_a13_flow_metrics.md` +
`opsaudit_a13_baseline_20260821.json` (wq#763, branch `feat/wq841-…-a1`) ·
`instructions/FLEET_OPS_AUDIT_2026-08-21.md`

Live checks taken during this pass, 2026-08-23 06:15–06:25Z:
`/home/lumen/instincts.yaml:17,96-101` · `/home/lumen/foreman/dispatcher/config.json` ·
`/home/lumen/foreman/harness/fworker.py:109-124` · `/home/lumen/bin/dispatch-lint:199` ·
`/etc/systemd/system/lumen-dispatch-lint.service:18` · `/home/lumen/bin/wq` (`--no-artifact`) ·
`systemctl is-active lumen-rerank.service` · `http://100.117.66.102:9100/metrics`
(node_exporter + 28 `lumen_queue_*` series) · `queue.db` read-only (A2 population 43,
blocked 14, `user` lane open+blocked 19).
