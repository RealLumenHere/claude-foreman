# EVENT_STREAM_DISPATCH_SPEC -- event stream + trigger matcher in front of wq (wq#4868)

Status: DESIGN ONLY. No code, no schema applied, no timers changed in this ticket.
Author: eval-foreman-patterns-b39338 (claude.ai orchestrator, 2026-11-05).
Parent research: scratch/research/paradev_ccc_patterns.md section 2 (Paradev
apps/api/src/lib/automations/*, packages/db schema.ts:1694-1990, MIT).
Implementation tickets: one per phase, filed by this session (section 9).

## 0. Verified state on Lumen, 2026-11-05 (read-only probes)

### Phase 0 decisions (wq#4892, agent-4892-2fcd7e, 2026-11-25 PST)

Design only: no code, no unit, no timer touched. Based on read-only probes on
Lumen, 2026-11-25 22:20-22:45 PST. Where this block and sections 2-10
disagree, this block wins.

| # | question | decision | Frank gate |
|---|---|---|---|
| D1 | tick host (3.9) | (a) `ExecStartPost` on `lumen-fleet-selfheal.service`. No `lumen-events.timer`. | YES, at wiring time only (G1 below) |
| D2 | events.db location | `/home/lumen/agents/work-queue/events.db`, as proposed | none |
| D3 | test fixture | Reuse the CAL-1 scratch-queue contract (real `bin/wq` + `WQ_DB` under a tmp root). Do not reuse the scratch git repo or the matrix runner in Phase 1. | none |
| D4 | package path (found in passing) | `foreman/events/` is taken. Code goes in `foreman/eventstream/`. | none; wq#4893 detail and liveness still name the old path |

**Corrections to the table below (verified live)**
- fleet-selfheal fires every 1 min, not 5: `lumen-fleet-selfheal.timer`
  OnUnitActiveSec=1min, AccuracySec=10s, live unit identical to
  ops/wq3354-fleet-selfheal (1min since its first commit a34608e7, 2026-10-01).
- The service is `User=root`, Type=oneshot, TimeoutStartSec=900, MemoryMax=512M,
  Nice=10, and already carries `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`.
  Sweep wall time 2-4 s in the passes sampled, one pass at 28 s.
- `lumen-foreman-dispatch.timer` is disabled/inactive today; the live broker is
  `lumen-auto-dispatch.timer` (2 min, User=lumen).
- `/home/lumen/foreman/events/` already exists (see D4).

**D1 tick host: selfheal ExecStartPost**
- Zero new timers. Option (b) existed only for sub-5-minute reaction; the host
  already runs every minute, so (b) buys nothing.
- Selfheal is always on. It does not honour DISPATCH_DISARM / DISPATCH_PAUSE, and
  the event record must keep filling while dispatch is disarmed (the stream's
  point is the record of what did NOT become a ticket). That rules out hosting
  the tick on auto-dispatch or foreman-dispatch.
- Selfheal is the first producer cut over (7.1), so its findings and the tick
  share one pass.

Binding constraints for the phases that build and wire it:
1. Line shape, placed AFTER the existing cron-stamp line:
   `ExecStartPost=-/usr/sbin/runuser -u lumen -- /usr/bin/timeout 40 /usr/bin/python3 /home/lumen/foreman/eventstream/events_tick.py`
   - `-` prefix: a tick failure never fails the selfheal unit.
   - after the stamp: a slow tick cannot delay selfheal's own dead-man stamp.
   - `runuser -u lumen`: the unit is root; wq's sqlite and events.db must never
     get root-owned WAL/SHM files (same rule as `selfheal_common.as_lumen()`).
   - `timeout 40`: a oneshot stays activating through ExecStartPost, so the next
     1-minute sweep waits on the tick. The cap bounds that delay.
2. Budgets in 3.4 and 3.6 were sized for a 5-minute host. New values: whole tick
   30 s internal wall budget (hard kill at 40 s), `wq add` subprocess timeout
   20 s, sweep LIMIT stays 100 but stops at the budget. Leftover rows are picked
   up one minute later.
3. The 300 s event_id bucket in 3.2 stays, as a dedupe window. It is no longer
   "the selfheal cadence". Schedule granularity (3.8) becomes 1 minute.
4. The tick writes its own dead-man stamp (`lumen-cron-stamp lumen-events-tick`)
   after a completed pass; the selfheal stamp says nothing about the tick. How
   lumen-cron-deadman enumerates stamps was NOT verified here: the wiring ticket
   checks it.
5. Kill switch: latch file `/home/lumen/autonomy/EVENTSTREAM_DISARM`, beside the
   DISPATCH_DISARM default path (dispatcher/circuit_breaker.py:66). Renamed from
   FOREMAN_EVENTS_DISARM so it cannot be confused with FOREMAN_EVENTS_DIR.
6. Wiring is NOT part of Phase 1. Phase 1 lands code and tests and ticks by hand
   (`events_cli tick --once --shadow`). The unit edit is its own ops ticket:
   change ops/wq3354-fleet-selfheal/lumen-fleet-selfheal.service by PR, root
   `install.sh`, then `install.sh --verify`. It is a root apply that sandboxed
   fworkers cannot run (wq#4940 / wq#5114 history). Rollback: reinstall the
   previous unit file, `systemctl daemon-reload`.

G1 FRANK GATE (wiring only): editing a root-run fleet-safety unit is Frank's
call, and section 10 question 1 already asks him. Recommendation: approve (a)
with constraints 1-6. Phase 1 (wq#4893) does not wait on it.

**D2 events.db: /home/lumen/agents/work-queue/events.db**
- The directory is lumen:lumen and already holds queue.db; WORKTREE_DELETE_SAGA.md
  puts worktree_saga.db there too. One place for wq-adjacent state, separate
  files so bin/wq stays sole owner of queue.db's schema.
- Backed up with no script change: backup-critical.sh section 5b (wq#3693) runs
  an online `.backup` of every `*.db` under /home/lumen/agents (maxdepth 3).
- Root LV has 153G free (66% used). /scratch is RAID0 (md1, assemble-only) and
  is wrong for state that needs queue.db-class durability.

Constraints: path overridable by env `FOREMAN_EVENTSTREAM_DB` (tests; mirrors
WQ_DB). File created 0600 lumen:lumen, because payloads are raw-ish provider
bodies. Opened only as lumen. Schema applied idempotently on first open. The
spec has no retention rule: Phase 1 adds a prune in the tick for settled
events and runs older than 90 days, which keeps the file small on the root LV.

**D3 test fixture: reuse the scratch queue, not the scratch repo**
What exists: harness/cal1_fixture.py (wq#619) `build|reset --root DIR` makes
scratch_repo + scratch_wq.db + events/_runs/worktrees and exports them through
`scratch_env()`; harness/tests/test_fworker_integration.py has the pytest
tmp_path version (`make_scratch_repo`, `make_scratch_env`, zero quota).
- This layer never touches git and never spawns a worker (section 6). Its only
  external effect is `wq add`. The part worth reusing is the scratch queue: the
  real /home/lumen/bin/wq with `WQ_DB=<tmp>/scratch_wq.db` (bin/wq:101).
- Phase 1 tests get their own conftest in foreman/eventstream/tests, per-test
  tmp_path, setting WQ_DB, WQ_AGENT, FOREMAN_EVENTSTREAM_DB,
  FOREMAN_NTFY_BIN=/bin/true, FOREMAN_JUDGE_SHADOW=0 and FLEET_SELFHEAL_WQ. Same
  key names as `cal1_fixture.scratch_env()` so a later end-to-end run can share
  one root. No import from harness/: its autouse conftest fixtures and 20-minute
  suite stay out of this package's test run.
- Guard: the conftest refuses to run when realpath of WQ_DB or the events db is
  under /home/lumen/agents/work-queue.
- NOT verified: whether `wq add` has side effects (aq, ntfy) that ignore WQ_DB.
  Only `_aq_unverified` (bin/wq:3074) was confirmed live-queue-only. Phase 1
  checks this before its first test that calls `wq add`.
- The full CAL-1 fixture (`cal1_fixture.py build --root`) is held for the
  Phase 2+ end-to-end case (event -> ticket -> fworker). `run-matrix` is never
  used here: it spends real quota.

**D4 package path: foreman/eventstream/**
`/home/lumen/foreman/events/` is the live fworker / action_gate JSONL directory
(FOREMAN_EVENTS_DIR default, harness/fworker.py:111, harness/action_gate.py:49),
owned tokeneng:token-client with ACLs, and `/events/` is in .gitignore line 5.
Code placed there cannot be committed. Every `foreman/events/...` code path in
sections 3.1, 3.3, 3.9 and 9 reads `foreman/eventstream/...`. The JSONL log
path (section 5) and the db filename are unchanged. wq#4893 must be read with
the new path; its liveness probe (`test -f foreman/events/pipeline.py`) needs
the same edit before that ticket can close.

| component | today | verified by |
|---|---|---|
| dispatch | lumen-foreman-dispatch.timer: OnBootSec=10min, OnUnitActiveSec=20min -> `dispatcher/foreman_dispatcher.py run --once`; stateless pass, scans OPEN tickets, spawns detached fworkers; never claims, never files | systemctl cat, module docstring |
| producers of tickets | fleet-selfheal sweep (lumen-fleet-selfheal.timer, 5 min) files selfheal tickets; aq escalations (`alert-queue/alerts.db` tables alerts/escalations/silences/group_rules) file tickets with `escalations.ticket_id`; sentinel probes (sentinel-probes.timer, DISABLED by Phase 1, wq#3751 pending); humans/orchestrators via `wq add` | ls, .schema, list-timers |
| alert dedupe today | `alerts(source, key, count, first_ts, last_ts, status)` -- a (source,key) coalescing store, no dispatched_at, no trigger config | .schema alerts |
| inbound HTTP | none in foreman for Gitea; Gatus -> ops/wq1184-gatus-health-checks/ntfy_relay.py -> ntfy; Commander/mcpw is MCP, not webhooks | grep -rln webhook |
| wq schema | `tasks(... status, block_reason, needs, lease_owner, fence, attempt, created_by, grant_req ...)`, indexes idx_tasks_claimable(lane,status,pri,id) | .schema tasks |
| queue sizes | open 60, claimed 12, blocked 82, frank_gated 55, done 4622 | sqlite count |
| rrule library | python-dateutil 2.9.0.post0 present on Lumen system python3 (rrule available) | python3 -c import |
| sqlite | 3.46.1 (partial indexes, CHECK with json_extract, upsert all supported) | python3 -c |
| timer policy | fleet is allergic to new timers (10-08 timer massacre, wq#3750/#3751); `lumen-cron-stamp` dead-man drop-ins on every oneshot | HANDOFF, unit drop-ins |

## 1. The gap, stated precisely

foreman dispatch is pull-only: something must already be a wq ticket before
the 20-minute scan can consider it. Every path that turns a world event into a
ticket (selfheal, aq escalation, a human) re-implements its own dedupe, its own
"did I already file this" memory, and its own idea of what the ticket should
say. There is no durable record of events that did NOT produce a ticket, so
"why didn't the fleet react to X" is unanswerable, and "probe files a ticket"
means policy lives in probe code instead of in a row an operator can edit.

Paradev's shape fixes exactly this: an immutable EVENT stream with dedupe at
the database, a pure TRIGGER matcher over stored config, a RUN row written
before any slow work and always settled, a grace-period re-dispatch sweep for
the one non-retryable hop, and schedules modelled as just another trigger
kind. In foreman terms the "publish to queue" hop is `wq add`; the fworker
spawn stays exactly where it is (foreman_dispatcher.py is unchanged by this
spec). The new layer sits BEFORE wq.

```
producers            ingest (normalise, insert-or-nothing)      match (pure)        publish            existing
gitea webhook  --+                                                                   +--> wq add -----> lumen-foreman-dispatch
gatus/ntfy_relay -+-> events_ingest.py --> events ---------> triggers --> runs ------+                 (unchanged)
selfheal sweep  --+   (one function)        unique(source,event_id)   CHECK(kind~config)  skipped_offline / dispatch_failed
aq escalation   --+                                                                        visible, never dropped
timer tick/rrule -+
```

## 2. Data model (SQLite, `/home/lumen/agents/work-queue/events.db`)

Separate file from queue.db for the same reason as the saga db (wq owns
queue.db's schema). WAL, busy_timeout 5000. All timestamps UTC
`%Y-%m-%d %H:%M:%S` (the format queue.db actually stores, wq#2669 lesson).

```sql
-- the "what": a ticket template, editable data, never code
CREATE TABLE automations (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  enabled      INTEGER NOT NULL DEFAULT 1,
  lane         TEXT NOT NULL,                     -- wq lane
  pri          INTEGER NOT NULL DEFAULT 3,
  title_tpl    TEXT NOT NULL,                     -- str.format over event fields, see 3.4
  detail_tpl   TEXT NOT NULL,
  liveness_tpl TEXT,                              -- wq --liveness probe, or NULL -> --no-probe reason
  needs        TEXT DEFAULT "",                   -- wq needs column passthrough
  target_node  TEXT,                              -- presence check target, NULL = Lumen
  reuse_open   INTEGER NOT NULL DEFAULT 1,        -- if an open/claimed ticket with same dedupe_key exists: note it, don't file
  dedupe_tpl   TEXT NOT NULL,                     -- e.g. "selfheal:{resource_key}" -> stored on the ticket (see 3.5)
  archived_at  TEXT,
  created_by   TEXT NOT NULL, created_at TEXT NOT NULL
);

-- the "when": one row per (automation, kind, filter)
CREATE TABLE triggers (
  id            INTEGER PRIMARY KEY,
  automation_id INTEGER NOT NULL REFERENCES automations(id),
  kind          TEXT NOT NULL CHECK(kind IN ("gitea","gatus","selfheal","aq","webhook","schedule","manual")),
  config        TEXT NOT NULL,                    -- JSON, validated by CHECK below + matcher
  enabled       INTEGER NOT NULL DEFAULT 1,
  next_run_at   TEXT,                             -- schedule kind only
  secret_hash   TEXT,                             -- webhook kind only: sha256 of per-trigger HMAC secret
  created_at    TEXT NOT NULL,
  CHECK (json_valid(config)),
  -- Paradev's "CHECK kind-matches-config", spelled for sqlite:
  CHECK ((kind =  "schedule") = (json_extract(config, "$.rrule") IS NOT NULL)),
  CHECK ((kind =  "schedule") = (json_extract(config, "$.tz")    IS NOT NULL)),
  CHECK ((kind =  "webhook")  = (secret_hash IS NOT NULL)),
  CHECK ( kind <> "schedule" OR next_run_at IS NOT NULL)
);
CREATE INDEX ix_triggers_match ON triggers(kind, enabled);
CREATE INDEX ix_triggers_sched ON triggers(next_run_at) WHERE kind = "schedule" AND enabled = 1;

-- immutable inbound stream: one row per delivery, dedupe at the DB
CREATE TABLE events (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,                   -- "gitea","gatus","selfheal","aq","schedule","webhook:<trigger_id>","manual"
  event_id       TEXT NOT NULL,                   -- provider id, or sha256(source|resource_key|minute-bucket) when provider has none
  kind           TEXT NOT NULL,                   -- normalised type: "pr.merged","endpoint.down","node.offline","alert.escalated","tick",...
  resource_key   TEXT,                            -- the thing it is about: repo#PR, endpoint name, node name, aq id
  title          TEXT, url TEXT, ref TEXT, actor TEXT,
  payload        TEXT NOT NULL,                   -- JSON, raw-ish, size-capped 64 KB
  received_at    TEXT NOT NULL,
  dispatch_input TEXT,                            -- JSON MatchableEvent, NULL = nothing dispatchable (ack-only)
  dispatched_at  TEXT,                            -- set ONLY after the publish step completed (3.3)
  UNIQUE (source, event_id)
);
CREATE INDEX ix_events_undispatched ON events(received_at)
  WHERE dispatched_at IS NULL AND dispatch_input IS NOT NULL;
CREATE INDEX ix_events_resource ON events(resource_key, received_at);

-- one row per (trigger, event) or (trigger, scheduled_for); written BEFORE slow work, always settled
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  trigger_id    INTEGER NOT NULL REFERENCES triggers(id),
  event_id      INTEGER REFERENCES events(id),
  scheduled_for TEXT,                             -- schedule kind, minute-bucketed
  status        TEXT NOT NULL CHECK(status IN ("dispatching","dispatched","dispatch_failed","skipped_offline","skipped_reuse","conflict")),
  error_code    TEXT,                             -- no_template, node_offline, wq_add_refused, publish_timeout, reuse_open, ...
  ticket_id     INTEGER,                          -- wq id when dispatched
  node          TEXT,
  created_at    TEXT NOT NULL, settled_at TEXT,
  CHECK ((event_id IS NULL) <> (scheduled_for IS NULL))
);
CREATE UNIQUE INDEX ux_runs_event    ON runs(trigger_id, event_id)      WHERE event_id IS NOT NULL;
CREATE UNIQUE INDEX ux_runs_schedule ON runs(trigger_id, scheduled_for) WHERE scheduled_for IS NOT NULL;
CREATE INDEX ix_runs_inflight ON runs(status, created_at) WHERE status = "dispatching";
```

## 3. Pipeline invariants (Paradev's ten, kept where they apply)

### 3.1 Normalise first, dispatch once
Each producer owns ONE normaliser: `normalise_<source>(raw) -> NormalizedDelivery`
= `{event: {...}, dispatch: MatchableEvent | None}` or `{skip: reason}`.
Permanent failures (unknown repo, revoked token, malformed) -> skip + ack
(a row with dispatch_input=NULL, dispatched_at=now). Transient failures (db
busy, disk) -> raise so the caller retries. Exactly one `ingest(delivery)`
and one `dispatch_matching(event)` exist, in `foreman/events/pipeline.py`.
Producers never write events rows themselves.

### 3.2 Insert-or-nothing dedupe at the DB
`INSERT ... ON CONFLICT(source, event_id) DO NOTHING`; `changes()==0` means a
redelivery: nothing dispatched, nothing logged beyond a debug line. Rows with
no product event get `dispatched_at = received_at` at insert ("nothing to
dispatch is dispatched"), so the sweep (3.6) never revisits them.
Providers without a native id (selfheal, gatus via ntfy) get a deterministic
event_id = sha256(source | kind | resource_key | floor(received_at / 300 s)):
one event per resource per 5-minute bucket, which is also the selfheal cadence.

### 3.3 Match is a pure function over stored config
`trigger_matches(config: dict, event: MatchableEvent) -> bool` in
`foreman/events/matching.py`, no I/O, 100% unit-testable. Candidate set =
`SELECT t.*, a.* FROM triggers t JOIN automations a ... WHERE t.kind=? AND
t.enabled=1 AND a.enabled=1 AND a.archived_at IS NULL [AND t.id=? for
webhook kind]` -- URL-addressed webhooks are pinned to their trigger id so
they never fan out. Config grammar (per kind): `{"filters": {"<event field>":
"<glob>|[list]|{"re": ...}"}, "min_priority": "...", "resource_re": ...}`.
Unknown filter keys are a config ERROR at write time (`events-cli trigger
add` validates) and a non-match at run time, never a match.

### 3.4 Publish is the only non-retryable hop, so it is the only thing recorded
Dispatch for one (trigger, event):
1. `INSERT INTO runs (status=dispatching) ON CONFLICT DO NOTHING` on the
   dedupe index. `changes()==0` -> outcome `conflict`, stop (another pass
   already owns it).
2. Presence (3.7). Offline -> settle `skipped_offline`, stop. Visible, not dropped.
3. Reuse check (3.5). An open/claimed ticket already carries this dedupe key ->
   `wq note <id> "event #N re-fired"` and settle `skipped_reuse`.
4. Render title/detail/liveness from templates (`str.format_map` over a
   frozen dict of event fields + trigger/automation ids + `scheduled_for`;
   missing key -> `dispatch_failed(no_template)`). Detail always ends with a
   provenance footer: `event:<id> trigger:<id> automation:<name>` and the
   dedupe key line (3.5). Control characters are stripped from every
   event-derived string before it reaches a ticket (Paradev's PTY scrub rule
   applied to the one human->agent text path we have).
5. `wq add --lane --pri --title --detail --liveness|--no-probe --needs
   --created-by events:<trigger_id>` via subprocess, 60 s timeout. The ticket
   id from stdout -> runs.ticket_id, status=dispatched.
6. `UPDATE events SET dispatched_at=now WHERE id=? AND dispatched_at IS NULL`
   -- only after step 5 returned an id. If `wq add` refuses (it refuses
   details containing `wq#NNNN` without --deps/--force: pass --force when the
   template cites a parent), settle `dispatch_failed(wq_add_refused)` with
   stderr in error detail; dispatched_at stays NULL so the sweep retries
   within the grace window -- but a refusal is permanent, so the sweep marks
   the event ack-only after 3 identical refusals.
A try/finally around 2-6 guarantees the run row is settled; a crash leaves a
`dispatching` row older than 10 min, which the sweep settles as
`dispatch_failed(crashed)` and re-evaluates the event.

### 3.5 Dedupe key on the ticket
wq has no column for it; the key goes in `detail` as a fixed last line
`dedupe: <key>` and in `subsystems` (free-text column already on tasks). The
reuse check greps `tasks.detail LIKE '%\ndedupe: <key>'` for
status IN (open, claimed, blocked, pending_review, frank_gated,
awaiting_grant). Phase 2 may add a real `tasks.dedupe_key` column via a
bin/wq migration if the LIKE scan shows up in timings (4,600 rows: it will
not, yet).

### 3.6 Re-dispatch sweep (60 s grace, single flight, time-budgeted)
Every pass: under slock `events-dispatch` (bin/slock, same mechanism the
dispatcher uses), `SELECT id FROM events WHERE dispatched_at IS NULL AND
dispatch_input IS NOT NULL AND received_at BETWEEN now-24h AND now-60s
ORDER BY received_at LIMIT 100`; re-run dispatch_matching for each with a
hard 60 s wall budget for the whole sweep (Paradev's comment: a 200-row
batch once outlived the lock). Rows older than 24 h are abandoned with an
aq row `events:undispatched:<id>` rather than retried forever.

### 3.7 Presence before dispatch, skipped_offline as a first-class outcome
`target_node` NULL means Lumen (always online from Lumen's view). Otherwise
presence = `fleet-ssh-check --nodes <node> --json` (existing tool, read-only,
sorts into ok/offline/...) cached 5 min in the pass. Offline -> run
`skipped_offline`, no ticket, no KILLED attempt, no selfheal guesswork. A
later `node.online` event (selfheal already detects transitions) re-fires
matching triggers whose last run for that resource was `skipped_offline`
(config flag `retry_on_online: true`).

### 3.8 Schedules are just another trigger kind
config = `{"rrule": "<RFC 5545 RRULE>", "dtstart": "...", "tz": "America/Los_Angeles"}`;
dateutil.rrule (present) computes occurrences. Each tick (3.9): bucket now to
the minute; `SELECT ... WHERE kind='schedule' AND enabled=1 AND next_run_at <= now`;
for each, synthesise an events row (source="schedule", event_id =
"<trigger_id>@<scheduled_for>", kind="tick") and dispatch with
scheduled_for = next_run_at; then ADVANCE next_run_at to the next occurrence
after now regardless of outcome. "Run now" (`events-cli trigger fire <id>`)
inserts a manual event and never touches next_run_at. Missed ticks while the
host was down are NOT replayed (next occurrence after now), matching
systemd's non-Persistent default the fleet already lives with.

### 3.9 Ticking without a new timer
The fleet does not want another timer. Options, decided in Phase 0:
(a) PREFERRED: extend `lumen-fleet-selfheal.service` (already every 5 min)
    with `ExecStartPost=/usr/bin/python3 /home/lumen/foreman/events/events_tick.py`
    -- ingest its own findings as events, run the sweep, advance schedules.
    5-minute schedule granularity is enough for everything a timer does today.
(b) a `lumen-events.timer` at 1 min if sub-5-minute reaction is ever needed
    (Gitea webhooks arrive push-style anyway, see 3.10, so (b) is unlikely).
Either way the tick is one-shot, slocked, dead-man stamped like every other
oneshot, and `FOREMAN_EVENTS_DISARM` (a latch file next to DISPATCH_DISARM)
makes every tick a no-op that logs `halt:disarmed`.

### 3.10 Inbound HTTP (Gitea, raw webhooks) -- Phase 4, needs a Frank gate
A new listening socket on the fleet is an energising change. Design:
`events_ingest_http.py` on Lumen, 127.0.0.1:<port>, exposed ONLY via a
Cloudflare-tunnel route gated by CF Access (same posture as mcpw), HMAC
`X-Gitea-Signature` verified against the per-trigger secret (sha256 stored,
secret in /home/lumen/.secrets/events-webhook-<trigger_id>), body cap 64 KB,
every request answered 2xx only AFTER the events row exists (so Gitea's
redelivery is harmless by 3.2). Gitea event ids: `X-Gitea-Delivery` header
(a UUID per delivery) -> event_id. Until Phase 4 lands, Gitea facts enter
via the existing gitea MCP / `lumen-pr` paths as `manual` events, or by a
poll normaliser in the tick (`gitea.pull_request.merged since last tick`),
which needs no socket and is where Phase 1 starts.

## 4. Outcome taxonomy mapped onto what foreman already has

| runs.status / error_code | today's equivalent | note |
|---|---|---|
| dispatched | a ticket exists | ticket_id recorded; provenance footer on the ticket |
| skipped_offline(node_offline) | selfheal files a ticket, fworker gets KILLED or CAPABILITY_DENIED trying to reach the node | the honest status the research called for |
| skipped_reuse(reuse_open) | selfheal's own "already filed" heuristics; aq count++ | one place, one rule |
| dispatch_failed(wq_add_refused / no_template / publish_timeout / crashed) | silent | surfaced as an aq row after 3 repeats |
| conflict | n/a | two passes raced; the dedupe index decides, no double ticket |
block_reason on the ticket itself (CAPABILITY_DENIED, PERMISSION_LOOP, ...)
is untouched: that is the attempt's outcome, this is the dispatch's.

## 5. Observability
- Every ingest/match/dispatch emits one JSONL line to
  /home/lumen/kb/logs/events-pipeline.jsonl (pass_start/pass_end, ingest,
  dedup_hit, match, run, sweep, halt) -- the same DecisionLog shape as
  foreman_dispatcher.py so existing log tooling reads it.
- `events-cli status`: undispatched count by age, runs by status last 24 h,
  schedules with next_run_at, triggers with 0 matches in 7 d (dead config).
- `events-cli why <ticket_id>`: walks ticket -> run -> event -> trigger ->
  automation, answering "why does this ticket exist".
- `events-cli why-not <event_id>`: lists candidate triggers and which filter
  rejected the event -- the question that is unanswerable today.

## 6. Safety and kill switches
- FOREMAN_EVENTS_DISARM latch file; STOP file honoured (same as dispatcher).
- Per-automation daily cap (`max_per_day`, default 20): a flapping probe
  cannot file 288 tickets; the 21st becomes `dispatch_failed(rate_capped)`
  and one aq row.
- Circuit breaker: more than 10 dispatch_failed in one pass -> halt pass,
  aq row, next tick retries (mirrors dispatcher's circuit_breaker.py).
- Shadow mode (Phase 1-2): `automations.enabled=0` rows still produce events
  and runs with status `shadow` is NOT a status -- instead shadow = a config
  flag on the tick that renders the ticket and logs `would_dispatch` without
  calling `wq add` (same as dispatcher --dry-run). Parity report compares
  would_dispatch rows against tickets the legacy producer actually filed.
- Nothing in this layer runs a model turn or spawns a worker.

## 7. What changes for existing producers (cut-over order)
1. fleet-selfheal: `file_ticket()` -> `events.ingest(normalise_selfheal(finding))`.
   Legacy filing stays on until the shadow parity report shows 0 misses for 7 d.
2. aq escalations: `escalate()` -> ingest(kind="alert.escalated",
   resource_key=f"aq:{source}:{key}"); `escalations.ticket_id` filled from
   runs.ticket_id. Silences stay in aq (they are an aq concern); the matcher
   just does not see silenced alerts because aq does not escalate them.
3. Sentinel probes (when re-enabled under wq#3751): emit events, never tickets.
4. Timers that only exist to run a recurring agent task (candidates:
   overnight research queue, fleet-refresh follow-ups, weekly audits) become
   schedule triggers; each retirement is its own ticket with the unit's
   dead-man stamp moved to the schedule's run row.
foreman_dispatcher.py, bin/wq, fworker: no changes in any phase.

## 8. Explicitly not adopted
- QStash / Cloudflare DO / Postgres: single-tenant, single-host; SQLite WAL
  + slock single-flight is the whole of it.
- A host relay for presence: fleet-ssh-check and selfheal's node.offline
  transitions already exist; a dial-out daemon is a separate design (wq#4864
  Agent Passport territory).
- Per-run JWTs: tickets already carry identity via wq claims and the lease
  fence; nothing here mints credentials.

## 9. Implementation phases (one ticket each, filed by this session)
| phase | deliverable | liveness | gate |
|---|---|---|---|
| 0 | decisions: tick host (3.9), events.db location, tests fixture; answers recorded at the top of this file | updated spec section 0 | none |
| 1 | foreman/events/{schema.sql,pipeline.py,matching.py,events_cli.py} + tests; selfheal normaliser in SHADOW mode (would_dispatch only); gitea poll normaliser read-only | `python3 -m pytest foreman/events/tests` green; events.db exists | PR via lumen-pr |
| 2 | cut-over selfheal + aq escalations to the matcher; legacy filing off after 7-day parity | parity report file + 0 misses | Frank notified, not gated |
| 3 | schedule kind: rrule triggers, tick advance, two existing recurring tasks migrated | runs rows with scheduled_for | each timer retirement is Frank-visible |
| 4 | HTTP ingest for Gitea webhooks behind CF Access | socket + HMAC test | FRANK GATE (new listener + CF route) |
| 5 | presence + skipped_offline + retry_on_online; events-cli why/why-not | skipped_offline rows observed on a known-offline node | none |

## 10. Open questions for Frank
- Tick host: fold into lumen-fleet-selfheal.service (preferred, zero new
  timers) or accept one `lumen-events.timer`?
- Should `skipped_offline` ALSO file a low-pri visibility ticket after N
  repeats, or is the aq row enough? (Proposal: aq row only.)
- Phase 4 at all, or keep Gitea pull-based via the tick? The poll costs one
  Gitea API call per 5 min and no new socket.
