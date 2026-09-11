# AUTO-3 — Foreman Auto-Dispatcher Design (wq#715)

Author: fleet BUILD worker (fable-5@high), 2026-08-21. Spec: wq#715 (10 MUST constraints).
Refs: /home/lumen/autonomy/dispatcher/lumen_dispatch.py (wq#175 t1 PoC), harness/spawner.py +
harness/concurrency.py + harness/fworker.py (T2a/T2b), orchestrator/router.py (T3 + wq#713
lane→repo), governor/foreman_usaged.py + governor/floors.json (T1), /home/lumen/bin/wq
(wq#582 claim --id, wq#502 identity, wq#661 effective_pri).
Status: DESIGN FOR REVIEW. Frank reads and approves this before the build phase starts.
The build is a separate, Frank-gated attempt; nothing in this document is implemented yet.

## 0. TL;DR

A **stateless-per-pass, one-shot scanner** run every 20 min by a systemd timer (STAGED,
disabled). Each pass: check kill switches → take a single-instance lock → scan open tickets
in an explicit lane allowlist → run every candidate through a fail-closed decision pipeline
(Frank-gate, fire-approval, deps, budget, lane caps, concurrency) → hand survivors to the
EXISTING dispatch stack (`router.dispatch_cmd` → `lumen-run start` → `fworker.py run`) →
log every decision as one JSONL line + a Langfuse trace → exit. The dispatcher **never
claims tickets and never runs model turns itself** — fworker claims (`wq claim --id`, all
guards enforced at the DB layer) and supervises the `claude -p` child. The dispatcher's
only side effect is spawning detached fworker units; everything else it does is read-only.

That division of labor is the single most important design decision: every safety property
the harness already enforces (governor gate, deps re-check, claim atomicity, lease/reaper,
guard.sh deny-list, wall-clock/turn watchdogs, verify-pass filing) is inherited rather than
re-implemented, and the dispatcher's own gates are a *second*, earlier layer — defense in
depth, not a parallel path that can drift.

## 1. What this extends, and what it deliberately does not copy

The wq#175 `lumen_dispatch.py` PoC established the pattern this ticket names: timer-driven,
fail-closed lane gate, kill-switch file, slock single-instancing, per-run ticket cap,
JSONL decision log, timer ships disabled (Tier-3 Frank enable). All of that carries over.

Two things do NOT carry over:

1. **Inline execution.** lumen_dispatch ran the model call in-process and enforced
   wall-clock/budget itself, because in July nothing else existed. Today fworker (T2a) is
   the per-ticket supervisor with strictly better enforcement (stream-json classifier,
   frame-gap watchdog, artifact verification, resume/retry, lease heartbeat). The
   dispatcher therefore spawns and exits; a pass takes seconds, not minutes, and a
   dispatcher crash mid-run strands nothing (§9 idempotence).
2. **Claim-then-gate.** lumen_dispatch claimed first and gated after, blocking the ticket
   on a gate failure. This dispatcher gates first, read-only, and never touches tickets it
   won't dispatch. A Frank-gated or unapproved-fire ticket is *skipped and logged*, never
   claimed and never mutated (exception: nothing — the dispatcher performs zero wq writes).

## 2. Components and file layout

```
repo (claude-foreman)                      deployed at /home/lumen/foreman/
├── dispatcher/
│   ├── DESIGN.md                          this document
│   ├── foreman_dispatcher.py              the scanner (build phase)
│   ├── config.json                        lane policy (checked in; §4)
│   ├── systemd/
│   │   ├── lumen-foreman-dispatch.service oneshot, runs one pass
│   │   └── lumen-foreman-dispatch.timer   20 min cadence, ships DISABLED
│   └── tests/test_dispatcher.py           unit tests (§11)
runtime state (never in git):
├── /home/lumen/foreman/dispatcher.log     JSONL decision log (ticket MUST 9)
├── /home/lumen/foreman/dispatcher/state.json   per-day lane counters + ntfy dedup (§6)
├── /home/lumen/foreman/dispatcher/runtime/health_state.json      dispatch_health.py dedup (§4)
├── /home/lumen/foreman/dispatcher/runtime/config_lint_state.json dispatch_config_lint.py dedup (§4)
├── /home/lumen/foreman/dispatcher/runtime/lane_state.json         wq-lane-init staged lanes (§4)
├── /home/lumen/foreman/approvals/wq<ID>.approved   fire-ticket approvals (Frank-created)
└── /home/lumen/autonomy/DISPATCH_DISARM   kill switch (shared with lumen_dispatch)
```

Reused, not rebuilt: `orchestrator/router.py` (classify/assign/dispatch_cmd + LANE_REPO),
`harness/spawner.py` (governor re-check + stagger + slot-capped batch),
`harness/concurrency.py` (live-unit counting, local-rung exclusion),
`governor/foreman_usaged.py` (can-dispatch + state.json), `/home/lumen/bin/wq` (claim
guards), `/home/lumen/bin/slock` (single-instance lock), `/home/lumen/bin/ntfy-send`.

## 3. The decision pipeline (one pass)

Ordered, fail-closed; the first refusing stage wins and is the logged `reason`. Stages
0–2 are per-pass; 3–10 run per candidate ticket.

```
0. kill switches      DISPATCH_DISARM or foreman STOP present → log, exit 0. Re-checked
                      between every ticket (a mid-run touch halts before the next spawn).
1. single instance    slock acquire foreman-dispatcher --ttl 30. Busy → log lock_busy, exit 0.
2. candidate scan     read-only: open tickets in config-enabled lanes, status=open,
                      ordered by effective_pri ASC then id ASC (wq#661 aging respected).
3. lane policy        lane enabled? within per-lane caps (§6)? lane mapped in LANE_REPO
                      (wq#713)? An unmapped lane is SKIPPED — never guess a repo. Sole
                      exception (wq#1094): 'auto' has no single LANE_REPO entry (repo
                      sweeps target different repos), but a repo-sweep ticket carries
                      its own target repo in its detail, written by repo_sweep.py — the
                      dispatcher reads that back (repo_sweep.repo_hint, validated
                      against the exact managed-repo set) instead of guessing. Any other
                      'auto' ticket still refuses.
3.6 nodispatch        title carries `[EPIC]` or `[DO NOT CLAIM]` → skip, never claim
                      (§4b). A rollup parent is a tracker, not work (wq#428).
4. Frank-gate         title/flags say Frank owns it → skip, never claim (§5). MUST 3.
5. fire approval      lane fleet-fires → approvals/wq<ID>.approved must exist, else
                      skip + one deduped ntfy (§5). MUST 4.
6. deps               every dep status=done (read-only wq show sweep). MUST 2.
7. already running    a live fworker-wq<ID> unit exists for this ticket → skip
                      (concurrency.list_running_units; closes the double-spawn window §9).
8. budget             governor can-dispatch(model) + floors.json margins via
                      router.governor_pressure: deny → skip; tight → tight-mode (§7). MUST 5.
9. concurrency        slots_available(exclude_local=True) against ceiling (default 3),
                      plus per-run spawn cap (default 3). No slot → stop selecting;
                      the next timer tick retries. Never waits. MUST 6.
10. spawn             router.dispatch_cmd(ticket) [repo derived from lane, MUST 7] →
                      lumen-run start → fworker claims with `wq claim --id` and takes
                      over. Spawn failure → log + ntfy, continue to next candidate.
```

Every stage — pass or refuse — emits one JSONL decision line (§8). A ticket the pipeline
skips is untouched in wq; it will be reconsidered from scratch next pass.

**TOCTOU story:** stages 2–9 are read-only and racy by nature (another agent may claim the
ticket between scan and spawn). That is safe by construction: the *authoritative* checks
all live inside `wq claim --id` (wq#582: deps, gate column, [DRAFT], live-claim,
subsystem slocks — "every claim-by-lane guard applies with NONE relaxed") and inside
fworker's own pre-claim governor/deps gates. If the dispatcher loses a race, fworker exits
CLAIM_ERROR / DEFERRED, the unit ends, and the decision log shows both halves. The
dispatcher's stages exist to avoid *wasting spawns and lease churn*, not to be the last
line of defense.

## 4. Lane policy config — dispatcher/config.json

Checked into the repo (an edit is a PR, reviewable), read at pass start, schema-validated;
an unreadable or invalid config **fails the pass closed** (log + ntfy, exit 1 — the timer
retries in 20 min; systemd OnFailure is not used to auto-heal config errors).

**Read from HEAD, never the working tree (wq#772).** `load_config()` resolves `config.json`'s
containing repo and runs `git show HEAD:<relpath>` — an out-of-band working-tree edit is
never live policy (no PR, no git record), and a `git clean`/checkout in that tree can never
silently revert live policy either, since the daemon was never reading the working copy to
begin with. There is no working-tree fallback: a repo git can't resolve (missing `.git`,
detached path, no HEAD) fails the pass closed exactly like a syntax error would. Every
`pass_start` log line carries `config_path` + `config_sha` so "what config is live" is
answerable from the log alone.

```json
{
  "cadence_note": "cadence lives in the systemd timer, not here",
  "max_spawns_per_pass": 3,
  "cloud_ceiling": 3,
  "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
  "lanes": {
    "foreman-tok": {"enabled": false, "max_per_day": 6},
    "pokemon":     {"enabled": false, "max_per_day": 4},
    "questfree":   {"enabled": false, "max_per_day": 4},
    "pstack":      {"enabled": false, "max_per_day": 4},
    "fleet-fires": {"enabled": false, "max_per_day": 2, "requires_approval": true}
  }
}
```

- **Allowlist, not blocklist.** A lane absent from `lanes` is never scanned. `user` is
  additionally refused by wq itself; `t1` stays lumen_dispatch's; `build`/`auto` stay with
  the current interim orchestrator until Frank explicitly moves them here.
- **Every lane ships `enabled: false`.** Enabling a lane is a config PR Frank merges —
  the same Tier-3 posture as the timer itself, one level finer.
- **Per-lane repo** is NOT in this file: it lives in router.py's `LANE_REPO` (wq#713),
  one source of truth. Enabling a lane whose repo is unmapped is a config error the
  dispatcher surfaces on every pass (skip + reason `lane_unmapped`) rather than guessing.
  Adding foreman-tok/lpp/questfree/pstack entries to LANE_REPO is a build-phase subtask
  **with Frank confirming each path** (open question §12.1).
- `requires_approval` is forced true for `fleet-fires` in code as well — a config edit
  cannot silently disarm MUST 4 (test §11/T4b).

**Mutable state lives under `dispatcher/runtime/` (wq#1253, #23), never in a tracked
file.** `health_state.json` (dispatch_health.py's alert-dedup ledger) and
`config_lint_state.json` (dispatch_config_lint.py's) write there now; both fall back to
reading their pre-migration path for one release if the new file is absent. `config.json`
itself is unaffected — it stays tracked and static, still read from HEAD per wq#772 above.
`dispatcher/runtime/lane_state.json` is where lane auto-registration stages a new lane
(`/home/lumen/bin/wq-lane-init`, wq#1282). It writes there instead of into `config.json`,
so opening a lane no longer dirties the tracked tree. Staging is not policy: a lane still
needs an explicit commit into `config.json` to become dispatch-effective, by the same
no-working-tree-fallback rule as `load_config()`. Nothing merges `lane_state.json` into
live config — `dispatch_config_lint.py` only *reports* the gap, naming the commit as the
remedy, so a staged-but-uncommitted lane is loud instead of silent.

That lint reads `config.json` **at HEAD**, the same way `load_config()` does. Reading the
working tree (its behaviour before wq#1282) made it blind to the drift it exists to catch:
`wq-lane-init`'s uncommitted edit satisfied the lint while the dispatcher, which only ever
sees HEAD, went on ignoring the lane's tickets — the wq#845 `build`-lane failure with an
extra step.

## 4b. Epic/tracker parents — never dispatched (wq#428)

A ticket whose title matches `\[epic\]|\[do[ -]?not[ -]?claim\]` (case-insensitive) is
skipped with stage `nodispatch`, reason `nodispatch_marker`, before the Frank-gate.

An epic parent is a rollup over child tickets: it has no acceptance criteria a build
worker can satisfy, so a lane that claims one can only come back with an empty diff.
wq#428 (`[EPIC][DO NOT CLAIM] LPP multiplayer`) burnt three lanes that way — every
attempt correctly produced nothing and was then judged FALSE_COMPLETE. The marker had
been in the title from the start; the dispatcher simply never read it.

Fail-closed and no override, exactly like the Frank-gate: the marker is the whole rule.
The brackets are load-bearing — `epic` as an ordinary adjective in a title is real work
(`test_nodispatch_marker_needs_brackets`). Close or convert an epic parent
administratively via `wq`; do not hand it to a lane.

## 5. Frank-gate and fire-ticket handling (MUST 3, MUST 4)

**Frank-gate — never claimed, fail-closed on signal ambiguity.** A ticket is skipped with
reason `frank_gated` when ANY of:

1. title contains `[FRANK GATE]` or `[manual]` (case-insensitive, anywhere in title);
2. title matches `owner=frank` (case-insensitive, `owner\s*=\s*frank`);
3. the wq row carries a non-empty `gate_type` and no recorded `frank_response` — the
   orchestrator's decision-gate columns; a pending decision means Frank has not ruled,
   and the dispatcher must not preempt him;
4. the wq `gate` column is non-empty and `gate_met()` would refuse — wq would refuse the
   claim anyway; skipping here avoids a doomed spawn and logs the reason legibly.

These are checked BEFORE anything else about the ticket is considered (only lane policy
runs earlier). There is deliberately no override flag.

**Fire tickets.** For lane `fleet-fires` (and any lane with `requires_approval: true`):
the dispatcher requires `/home/lumen/foreman/approvals/wq<ID>.approved` to exist at
decision time. Per-ticket, created by Frank by hand — presence is consent, content is
ignored (same contract as STOP/DISARM). Without it: skip with reason
`fire_unapproved`, and notify Frank via ntfy **once per ticket per 24 h** (dedup ledger in
state.json §6) so an unapproved fire pings him but never floods him every 20 minutes.
With it: the ticket continues down the normal pipeline — approval does not bypass deps,
budget, or concurrency. The approval file is not consumed or deleted by the dispatcher;
Frank removes it (or the ticket closes) to end its effect.

## 6. Budget awareness and lane caps (MUST 5)

Three layers, all consulted per pass:

1. **Governor verdict (authoritative):** `foreman_usaged.py can-dispatch <model>` — exit
   non-zero, STOP file, or unreachable/stale state (its own fail-closed rules, wq#613) →
   skip with reason `governor_deny`. Re-checked by spawner immediately before each spawn
   and by fworker before claiming, so a mid-pass floor breach still bites.
2. **Floors + soft margin:** `router.governor_pressure` (floors.json:
   session ≥20 %, weekly_all ≥15 %, Fable scoped floor per its schedule; soft margin 15
   points) → `ok`/`tight`/`deny` per model. `deny` skips. `tight` flips the pass into
   **tight-mode**: `max_spawns_per_pass` drops to 1 and only tickets at
   `pri <= tight_mode.min_pri` are eligible; effort-first downgrade inside the rung is
   already the router's job (T3 doctrine), not duplicated here.
3. **Lane caps (dispatch-count proxy):** `max_per_day` per lane, counted in
   `dispatcher/state.json` (`{"day": "2026-08-21", "lane_counts": {...},
   "fire_ntfy": {"710": "<iso ts>"}}`, atomic tmp+rename, UTC day rollover resets
   counts). Cap reached → skip with reason `lane_cap`. Honest limitation, stated rather
   than hidden: per-lane *spend* cannot be read from /api/oauth/usage (pools are global,
   not per-lane), so the enforceable cap is dispatch count; actual per-lane USD/tokens are
   observable in Langfuse (LiteLLM already ships generations + costs to
   langfuse-clickhouse) and CAL-1 measurements can later turn counts into calibrated
   budgets. If Frank wants hard USD caps per lane now, the lever is per-ticket
   `--max-budget-usd` on the fworker child — noted as an open question (§12.3), not
   silently designed in.

## 7. Concurrency bound (MUST 6)

Reuse `concurrency.slots_available(prefix="fworker-wq", ceiling=N, exclude_local=True)` —
live systemd units, not wq claims, are the ground truth for "how many `claude -p` are
running against the shared login", and local-rung workers never eat a cloud slot (T4).
Ceiling default 3 (`config.json cloud_ceiling`, FOREMAN_RESEARCH §8). The dispatcher
takes `min(slots, max_spawns_per_pass, remaining lane caps)` candidates and spawns them
via `spawner.dispatch_batch(..., slot_wait_max_s=0)` — the existing stagger (2–3 s) and
per-spawn governor re-check come free; `slot_wait_max_s=0` because a scheduled pass never
waits for capacity (the next tick is 20 min away and waiting would hold the slock).
`lumen-run` unreachable → `LumenRunError` → treated as zero slots (fail toward
under-subscription, same as spawner).

## 8. Logging + tracing (MUST 9)

**dispatcher.log** — append-only JSONL at `/home/lumen/foreman/dispatcher.log` (the
ticket-named path), one object per decision and per pass boundary:

```json
{"ts": "...", "pass_id": "fdisp-20260821T0420Z-1234", "event": "skip|spawn|pass_start|pass_end|error",
 "ticket": 715, "lane": "foreman-auto", "stage": "frank_gate", "reason": "title [FRANK GATE]",
 "model": "claude-sonnet-5", "unit": "lumen-job-fworker-wq715-....service"}
```

Same stream to stdout → journald (belt and braces). Rotation via a staged logrotate
stanza (weekly, 8 rotations, copytruncate) — not size-managed in-process.

**Langfuse trace** — one trace per pass (`name=foreman-dispatch`, id = pass_id), one span
per candidate ticket carrying the decision + reason, tagged with lane/model/pressure.
Emitted via the Langfuse ingestion API on the existing local instance (host + keys from
`/home/lumen/.secrets/langfuse-foreman.env`, 0600; path env-overridable for tests).
**Best-effort by contract:** 5 s timeout, any failure logs `langfuse_emit_failed` and
never blocks, fails, or delays a pass; missing keys → log-only degradation. Worker-side
LLM spend is already in Langfuse via LiteLLM for local-rung runs; this adds the
*decision* layer so "why did/didn't wq#N dispatch" is queryable next to cost.

**ntfy on failure** — spawn failure, unhandled pass exception, invalid config, and
`fire_unapproved` (deduped): via `/home/lumen/bin/ntfy-send`, priority high for the first
three. Routine skips (deps, budget, cap) are log-only — they are the steady state.

## 9. Idempotence and crash behavior

A pass holds no durable state mid-flight except state.json counters (written after each
spawn, atomically). Crash points:

- **Before any spawn:** nothing happened; slock expires (ttl 30 min) or is released by
  the finally block; next tick is clean.
- **After `lumen-run start`, before logging:** the fworker unit exists and owns the
  ticket lifecycle end-to-end (claim → done/release/block). The dispatcher's stage-7
  already-running check makes the next pass skip the ticket; even if that raced, the
  second fworker's `wq claim --id` refuses on the live lease and exits CLAIM_ERROR. At
  most one worker ever holds a ticket — enforced by wq, not by dispatcher bookkeeping.
- **Counter drift** (spawn succeeded, count lost): bounded by one ticket per crash, and
  self-corrects at day rollover. Accepted; caps are rate limiters, not invariants.

The pass is safe to run manually at any time (`foreman_dispatcher.py run --once`) —
converges to the same end state regardless of timer state or prior partial runs. A
`--dry-run` mode runs stages 0–9 and logs `would_spawn` without side effects (also the
integration-test entry point).

## 10. Scheduling + staged deploy (MUST 1, MUST 10)

`lumen-foreman-dispatch.service` (Type=oneshot, `ExecStart=/usr/bin/python3
/home/lumen/foreman/dispatcher/foreman_dispatcher.py run --once`, User=lumen,
TimeoutStartSec=600, no special privileges) + `lumen-foreman-dispatch.timer`
(`OnBootSec=10min`, `OnUnitActiveSec=20min`, `RandomizedDelaySec=60`,
`Persistent=false` — a missed window is skipped, never replayed in a burst). Cadence
changes = drop-in override or unit PR, both Frank-visible.

**STAGED (Tier-3, same as lumen-dispatch.timer):** unit files live in the repo under
`dispatcher/systemd/`; the build phase installs them to /etc/systemd/system **without
enabling** (`systemctl daemon-reload` only — note the house deny-list blocks
stop/disable/mask but not install; if the guard also refuses the install step, the units
stay repo-only and DESIGN.md's install block becomes Frank's copy-paste). Nothing runs
until Frank runs `systemctl enable --now lumen-foreman-dispatch.timer` himself. Rollback
= `touch /home/lumen/autonomy/DISPATCH_DISARM` (instant, no systemd interaction needed)
or disable the timer.

## 11. MUST → unit test mapping (acceptance gate)

Tests live in `dispatcher/tests/test_dispatcher.py`, stdlib unittest, **zero quota, zero
live services**: `wq` is exercised against a scratch `WQ_DB` sqlite file (the real
/home/lumen/bin/wq binary, same fixture pattern as fworker's tests), `lumen-run` /
`ntfy-send` / governor / Langfuse are stub executables or injected callables, roots
overridden via the existing `FOREMAN_*` env seams plus new `FOREMAN_DISPATCHER_*`
(config path, state path, approvals dir, disarm path, log path).

| # | MUST | Test(s) — all assert on the decision log + absence/presence of spawn calls |
|---|------|------|
| 1 | Scheduled | `test_units_staged_disabled`: unit files parse (systemd-analyze verify if present, else key assertions: oneshot, timer 20 min, not enabled by any [Install] side effect); `test_once_pass_exits`: one pass terminates without a timer. |
| 2 | Deps-aware | `test_deps_unmet_skipped`: candidate with an open dep → `skip/deps`; dep flipped done → spawned next pass. `test_wq_claim_backstop`: dispatcher bypassed (spawn forced), fake fworker's `wq claim --id` still refuses. |
| 3 | Frank-gate | `test_frank_gate_title_variants`: `[FRANK GATE]`, `[manual]`, `owner=frank`, case-mixed → all `skip/frank_gated`, zero wq writes (DB before == after). `test_pending_gate_type_skipped`: gate_type set, frank_response empty → skipped; response recorded → eligible. |
| 3b | Nodispatch (§4b) | `test_nodispatch_title_variants`: `[EPIC][DO NOT CLAIM]`, `[epic]`, `[Do Not Claim]`, `[DO-NOT-CLAIM]`, `[DONOTCLAIM]` → all `skip/nodispatch_marker`, zero wq writes (DB before == after). `test_nodispatch_marker_needs_brackets`: `epic` as a plain adjective still spawns. |
| 4 | Fire approval | `test_fire_requires_approval`: fleet-fires ticket, no file → `skip/fire_unapproved` + exactly one ntfy; second pass same day → no second ntfy; file created → proceeds to later stages. `test_fire_approval_not_disarmable`: config `requires_approval: false` on fleet-fires → still enforced. |
| 5 | Budget-aware | `test_governor_deny_skips`: stub can-dispatch exit 1 → no spawn. `test_tight_mode`: pressure tight → max 1 spawn, pri-2 ticket ineligible. `test_lane_cap`: max_per_day=1, two candidates → one spawn, one `skip/lane_cap`; counter resets on day rollover. |
| 6 | Concurrency | `test_ceiling_respected`: 3 running units stubbed → zero spawns; 2 running → one spawn. `test_local_excluded`: local-rung unit doesn't consume a slot. `test_lumen_run_unreachable_is_full`: list failure → zero spawns. |
| 7 | Router routing | `test_repo_from_lane`: spawn command line comes from router.dispatch_cmd with lane-derived repo; `test_unmapped_lane_skipped`: enabled lane absent from LANE_REPO → `skip/lane_unmapped`, never `/home/lumen`. wq#1094: `test_auto_lane_repo_sweep_ticket_dispatches_via_repo_hint`: 'auto' repo-sweep ticket dispatches via its own detail's repo, never LANE_REPO; `test_auto_lane_non_sweep_ticket_still_unmapped`: a non-repo-sweep 'auto' ticket still refuses. |
| 8 | Kill switch | `test_disarm_at_start`: DISPATCH_DISARM present → pass exits, zero decisions beyond `disarmed`. `test_disarm_midrun`: file created between candidates (stub hook) → later candidates never evaluated. `test_stop_file_equivalent`: foreman STOP honored identically. |
| 9 | Logged | `test_every_decision_logged`: each candidate yields exactly one JSONL line, schema-checked; `test_langfuse_best_effort`: emitter raising → pass still succeeds + `langfuse_emit_failed` logged; `test_ntfy_on_spawn_failure`. |
| 10 | Staged deploy | covered by test #1's staged/disabled assertions + `test_dry_run_no_side_effects`: --dry-run leaves DB, state.json, and spawn stub untouched. |
| — | Single instance | `test_slock_busy_noop`: lock held → pass logs `lock_busy` and exits 0 without scanning. |
| — | Already-running | `test_running_unit_skips_ticket`: live fworker-wq<ID> unit → `skip/already_running`. |

Adversarial posture mirrors T2a §12: every gate test also asserts the *negative* (the
spawn stub records zero calls), not just the log line — a gate that logs "skip" and
spawns anyway must fail the suite.

## 12. wq self-heal (wq#725 AUTO-7) — a separate scanner, same contract

`dispatcher/self_heal.py` is a second, independent one-shot scanner (own systemd unit,
own 10-min timer, own slock scope `foreman-selfheal`) that closes the loop this
dispatcher only opens: a ticket the dispatcher spawns can still end up `blocked` if
fworker exhausts `MAX_ATTEMPTS` (harness/fworker.py `handle_failure()`). Before wq#725,
every blocked ticket looked the same — a broken spawn environment (wrong `--repo`, a
stale worktree from a prior crashed attempt: wq#704/wq#705) sat blocked exactly like a
ticket the worker genuinely couldn't do, until Frank noticed by hand.

The fix lives mostly in the harness, not here: `handle_failure()` now prefixes every
`wq_block()` reason with a class from `classify_block_reason()` — `INFRA_CRASH:` (spawn/
process environment broke, no real work attempted), `LIMIT_HIT:` (turn/budget ceiling —
a resourcing call), or `WORK_BLOCKED:` (the worker/model decided it can't do the work,
or an unrecognized shape — the conservative default). self_heal.py only ever acts on
`INFRA_CRASH:`: `wq release` it, subject to a per-ticket attempt cap read from fworker's
own `attempt-N.json` files, and a "how long has this been stuck" age check read from wq's
own `block_reason`/`blocked_at` columns (wq#741 — added to `tasks` the same additive
ALTER-TABLE-if-missing way as `lease_expires`/`subsystems`/`gate`; `cmd_block()` populates
them alongside `notes`, and clears them on release/done). `LIMIT_HIT` and `WORK_BLOCKED`
are never released — no override.

Released is as far as self_heal.py goes: it does not spawn. A released ticket is `open`
again, which makes it a normal candidate for *this* dispatcher's own next pass — the
same "gate here, spawn there" division of labor as §1, just with an extra scanner ahead
of it. See `dispatcher/self_heal.py`'s module docstring for the full contract and
`dispatcher/tests/test_self_heal.py` for coverage.

## 12. Decisions Frank must ratify (with the design, or as build-phase red lines)

1. **LANE_REPO entries** for foreman-tok / lpp / questfree / pstack (and fleet-fires):
   each lane's canonical repo path must be confirmed by Frank before that lane can be
   enabled — the dispatcher refuses unmapped lanes, so wrong-guess risk is zero, but
   without entries the dispatcher dispatches nothing outside existing mappings.
2. **Lane cap defaults** in §4's config (6/4/4/4/2 per day) are placeholders scaled to
   nothing but intuition; Frank sets the real numbers at enable time (config PR).
3. **Per-lane hard USD caps:** dispatch-count caps + Langfuse observability (§6.3) vs.
   also passing `--max-budget-usd` per fworker child. The latter needs a per-lane price
   column and fworker plumbing — proposed as a follow-up ticket, not in this build.
4. **Langfuse keys**: a scoped key pair for the dispatcher at
   `/home/lumen/.secrets/langfuse-foreman.env` needs Frank to mint it (secrets are
   guard-denied to workers). Until it exists the dispatcher runs in log-only degradation
   — acceptable, or a blocker for enable?
5. **tight_mode policy** (§6.2: 1 spawn, pri 1 only) — alternative is "no spawns under
   tight". Current choice keeps p1 work flowing while floors still protect Frank's
   interactive share; the governor deny floor remains absolute either way.

## 13. Explicit non-goals

- No ticket mutation by the dispatcher (no claim/block/release/edit) — one exception
  considered and REJECTED: auto-blocking Frank-gated tickets would mutate state Frank
  owns and lumen_dispatch's claim-then-block pattern is exactly what §1.2 removes.
- No retry/rescue logic — fworker owns attempts; reaper.py owns stale leases.
- No t1 lane takeover — lumen_dispatch keeps running until Frank retires it.
- No in-dispatcher model selection — router.py owns the tuple; the dispatcher never
  passes --model/--effort overrides.
- No waiting: for slots, for approvals, for governor recovery. A pass answers "what can
  dispatch right now", and 20 minutes later the question is asked again.

## 14. Weekly-budget pacing (wq#716 AUTO-4)

§6 layer 2 (`router.governor_pressure`) is reactive: it floors on *remaining* percentage
of each pool (e.g. Fable weekly-scoped remaining >= its floor per floors.json), so it
only bites once most of the week's budget is already gone — however fast it got there.
That let the 2026-08-13..08-20 week burn 99% of Fable in the first 36 hours (~21% of the
week) while the floor sat untouched the whole time: reactive-only budget awareness has no
notion of *pace*.

`dispatcher/pacing.py` adds a forward-looking layer on top, run as stage 8b (right after
§6's tight-mode filter, still inside the existing budget stage): for each weekly-scoped
pool a candidate's model draws from (`weekly_all` always, `fable_weekly_scoped` only for
Fable models), project the current burn rate out to that pool's reset
(`usage_pct / elapsed_fraction_of_week`) and compare against a policy's thresholds:

- **conservative** (default): projected >= 90% by reset -> `delay` (only `pri <=
  delay_min_pri` tickets still dispatch this pass); projected >= 130% -> `skip` (nothing
  on that pool dispatches this pass, any priority).
- **aggressive**: same two-tier shape, looser thresholds (120% / 180%) for a fleet that
  wants to ride closer to the edge.
- **off**: pacing verdict is always `ok` — pure escape hatch, e.g. while calibrating
  thresholds against real burn curves.

Policy and thresholds live in `dispatcher/pacing_config.json` (`FOREMAN_PACING_CONFIG`
to override the path), loaded once per pass. An unreadable or invalid file is *not* a
pass-failure — the governor's own floor check is already the fail-closed layer (§6
layer 1/2); pacing is additive, so a broken pacing config falls back to the in-code
conservative default and logs `info/pacing_config_invalid` rather than blocking the
whole pass. Too little data to trust a projection (pool missing `resets_at`, or under
`min_elapsed_fraction` — default 2% of the week, ~3.4h — into the window) is `None`,
never guessed into a verdict.

Guardrails (both load-bearing, both unit-tested):

- **Never blocks a Frank-approved fire ticket.** Any ticket that already cleared stage
  5's fire-approval gate (`fleet-fires`, or any lane with `requires_approval: true`) is
  passed straight through pacing (`exempt=True`) — Frank saying yes is not something an
  automated pacer should second-guess.
- **Bounded to this pass, never permanent.** Pacing holds no cross-pass state beyond an
  ntfy-dedup ledger (`state.json.pacing_ntfy`, keyed by pool, pruned like `fire_ntfy`);
  the projection is recomputed from scratch every pass, so a pool that cools off
  un-throttles on the very next tick with no manual reset required.

Frank-visible signal: `handle_pacing_throttle` fires one deduped ntfy per pool
(`ntfy_dedup_hours`, default 6h) naming the pool, the action, and the projected
percentage — so a slowdown is legible as "pacing did this, here's why" rather than
tickets silently going quiet. `foreman_dispatcher.PACING_HOOK` is a seam
(`= pacing.decide`, mirroring `LANGFUSE_EMIT`) so tests can swap the hook without faking
governor state end-to-end. See `dispatcher/pacing.py`'s module docstring and
`dispatcher/tests/test_pacing.py` (pure module logic) / the `wq#716` block in
`dispatcher/tests/test_dispatcher.py` (dispatcher-wired: skip/delay/exempt/ntfy-dedup/
config-fallback) for the full contract.

## 15. Observability, circuit breaker, runaway detection (wq#718 AUTO-6)

Three read-mostly additions on top of the §3 pipeline, none of which change what MUST
1–10 above already guarantee — they add a dead-man's switch and visibility, they don't
touch the claim/spawn contract.

**Circuit breaker** (`circuit_breaker.py`) — a new stage 0.5 in `run_pass()`, checked
right after `kill_switch()` and *before* `load_config()` so a broken config.json can
never suppress a trip. Reads harness/fworker.py's own `attempt-N.json` records (the
same ledger self_heal.py already treats as authoritative, §12) and computes the
fraction classified LAUNCH_FAILURE/KILLED/OOM (fworker's own INFRA_CRASH bucket, §12 —
not a broader "any non-success" rate; see circuit_breaker.py's module docstring for why
that would be dominated by ordinary multi-attempt-ticket noise) over a trailing window.
Above `FOREMAN_BREAKER_MIN_SAMPLES` samples and `FOREMAN_BREAKER_THRESHOLD` rate, it
`disarm()`s: **touches DISPATCH_DISARM unconditionally first**, then best-effort
notifies (notify_sinks.py: ntfy urgent + notify-send over SSH to Frank's desktop). The order
is the guardrail: a notify failure can never suppress or appear to undo the disarm that
already happened. The same DISARM file is the dispatcher's own kill switch (MUST 8), so
the very next pass halts at stage 0 without the breaker needing to run again.

**Runaway detector** (`runaway.py`) — a new stage 3.5, right after a candidate's status
is confirmed `open`. `stuck_ticket_ids()` flags any ticket with
`>= FOREMAN_RUNAWAY_MAX_ATTEMPTS` finished attempts that hasn't reached `done` (reuses
self_heal.py's `attempt_count()`/`wq_show()` rather than a second counter). A stuck
ticket is skipped every pass (never blocked, never edited — §13's "no ticket mutation"
still holds) and Frank is ntfy'd once per 24h via the same dedup ledger pattern as §5's
fire-approval ntfy (state.json's new `runaway_ntfy` key, alongside the existing
`fire_ntfy`).

**Spawn-loop guard** (`spawn_loop_guard.py`, wq#1703) — a new stage 3.5c, after the
runaway detector above and the wq#1415 harness-defect hold. Both of those key on an
artifact the *worker* writes (attempt-N.json, failure.json); a worker that dies before
writing either is invisible to both, which is how wq#1543 spawned 31 times and wq#1572
23 times in three hours on 2026-09-10 (ISSUE-LOG #283) with nothing to show for it. This
guard counts the dispatcher's own spawns instead: stage 10 appends `{ts, attempts}` to
state.json's `spawn_ledger` after each real spawn (`attempts` = the finished-attempt
count at that moment), and `looping()` refuses a ticket that reached
`FOREMAN_SPAWN_LOOP_MAX` (3) spawns inside `FOREMAN_SPAWN_LOOP_WINDOW_H` (3h) without
the attempt count moving. The refusal skips as `spawn_loop` (structural, so the wq#1410
watchdog doesn't read a working breaker as a stall), opens one `aq add` row
(source `foreman-dispatcher`, key `spawn-loop-wq<id>` — aq's own dedup bumps it rather
than duplicating) and ntfy's once per 24h on the `spawn_loop_ntfy` ledger. Like §15's
other breakers it never mutates the ticket, and it clears itself two ways: a finished
attempt lifts it immediately (handing the ticket to the attempt-count breaker above), or
the burst ages out of the window. The ledger is pruned in `load_state()` and survives the
UTC day rollover that resets `lane_counts` — a window that reset at midnight would hand a
looping ticket a fresh budget every night.

**Dashboard** (`dashboard_data.py` + `dashboard.py`) — a stdlib-only, GET-only HTTP
server (no new dependency, no do_POST/PUT/DELETE handler at all — there's no code path
to turn it into a control surface even by accident) serving the snapshot the ticket
asked for: running workers, per-lane queue depth (open/eligible/blocked — "eligible"
reuses `nodispatch_reason`/`frank_gate_reason`/`unmet_deps` from foreman_dispatcher.py
directly, so it
always means what dispatch means by it), rolling failure rate (24h/7d, the broader
"did the attempt succeed" definition — distinct from the breaker's narrower trip
condition above), circuit-breaker armed state, stuck tickets, and governor
budget/floor state. Ships STAGED (systemd/lumen-foreman-dashboard.service, disabled,
binds 127.0.0.1 only) — reaching it at a real hostname behind Cloudflare Access is DNS
+ Cloudflare-account work outside a worker's access; systemd/INSTALL.md documents the
steps Frank runs by hand.
