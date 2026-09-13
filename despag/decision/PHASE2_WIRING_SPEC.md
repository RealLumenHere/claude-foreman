# de-spaghettifier — PHASE 2 WIRING SPEC
_2026-09-13 · wq#1883 · research only, no code changes · validates the shape Frank decided in the
orch chat of 2026-09-12_

Inputs read: `foreman/harness/{fworker.py,classifier.py}`, `foreman/despag/**`,
`foreman/dispatcher/burn_rate.py`, `foreman/ops/{systemd_desired_state.py,wq1184-gatus-health-checks/}`,
`/home/lumen/bin/{wq,lumen-wq-health}`, `/home/lumen/research/harnessrouter-ref/src/{gateway,runner}`,
`kb/ISSUE-LOG.md`, `decision/PHASE0_WIRING_SPEC.md`, tickets wq#1887/1888/1889. Line numbers are
against these trees as of this date.

Path shorthand: FW = `foreman/harness/fworker.py`, CL = `foreman/harness/classifier.py`,
HR = `research/harnessrouter-ref/src`.

Verdict per decision: **A CONFIRMED (with one unverifiable input)**, **B CONFIRMED but the stated
seam understates the work — a gateway prerequisite is mandatory**, **C CONFIRMED, signal is live,
one line-reference corrected**, **D CONFIRMED on all five**, **E CONFIRMED, with a sharper risk than
the ticket states**.

---

## A. DEPLOY LOOPBACK-ONLY, NO TUNNEL — CONFIRMED (wq#1887)

### A.1 `harness/systemd/` is already a staging dir the drift checker walks

`ops/systemd_desired_state.py:119-130` — `staged_paths()` globs
`{FOREMAN}/*/systemd/*.{service,timer,socket,path,target,mount}`. `harness/systemd/` already matches
and already holds `lumen-liveness-clear.{service,timer}`, `lumen-read-dir-sweep.{service,timer}`,
`wq-worker@.service`. Staging a despag unit there costs nothing and buys the existing check.

`staged_drift()` (`:145-163`) reports `missing` (staged, never installed), `stale` (installed,
content differs) and `disabled`. The comment at `:112-117` names why this exists: `diff()` only
walks the live side, so a unit committed and never installed read CLEAN while the feature it gated
was dead — the wq#1404 and wq#1482 class. Staging turns "never installed" into a state the fleet can
name.

**Constraint the ticket must respect, and does.** `:127` — `if "@" in b or not b.startswith("lumen-"):
continue`. A unit not named `lumen-*` and any `foo@.service` template is invisible to `staged_drift()`.
wq#1887 specifies `lumen-despag.service`, which passes both filters. Naming it `despag.service` would
have silently opted out of the only check that proves it got installed.

**Gap worth knowing:** `:161` applies the `disabled` check to `.timer` only. An installed-but-not-
enabled `.service` reads CLEAN. So `staged_drift()` proves *installed*, never *running*. That is
exactly why the Gatus probe is not redundant with it — the two decisions in wq#1887 cover different
failure modes, and both are needed.

### A.2 The Gatus probe has an exact template to copy

`ops/wq1184-gatus-health-checks/config.yaml:109-120` (`foreman-heartbeat`) is the shape: loopback
`url`, `interval: 5m`, `conditions: ["[STATUS] == 200"]`, `type: custom` alert with
`failure-threshold: 3`, `success-threshold: 2`, `send-on-resolved: true`. `config.yaml:10` constrains
every probe to HTTP(S), which `GET /v1/uhp` satisfies.

`GET /v1/uhp` is a correct probe target: `despag/app.py:367-395` builds a static `Discovery` document
and returns it. No auth, no spawn, no backend call.

Two notes for the implementer:

- `app.py:394` attaches `body["lumen"] = {"budget": admission.budget_snapshot()}`, so each probe also
  does a governor-state read. That read is cached (`admission._read_governor_state`), and at a 5-minute
  interval the cost is noise. Because the deployment is loopback-only, the budget numbers never leave
  the box — the vendor extension is safe here in a way it would not be behind a tunnel.
- `config.yaml:105-108` already documents the precedent that matters most for ordering: the
  `foreman-heartbeat` probe reads FAIL until an operator enables the unit by hand, and the config says
  so inline. despag has the identical property (install is the operator's sudo step, per wq#1887).
  Copy that comment, or the first alert after landing is a false positive that teaches the fleet to
  ignore the probe.

### A.3 Env from a 0600 file — consistent with what the code already expects

`despag/app.py:106` — `DESPAG_DATA_DIR = Path(os.environ.get("DESPAG_DATA_DIR", "~/despag-data"))`,
with `CONTROL = ControlStore(str(DESPAG_DATA_DIR / "control.db"))` at `:107`. An `EnvironmentFile`
carrying `DESPAG_DATA_DIR` and `DESPAG_API_TOKEN_FILE` is the natural fit; the token stays a *path*,
never a value in the unit, which matches instinct 4.

### A.4 `DESPAG_DATA_DIR` under restic — the one thing this spec CANNOT verify

wq#1887's acceptance says to read `services/backup/scripts/backup-critical.sh` `PATHS_TO_BACKUP`.
Both that path and `/home/lumen/backups` are outside a BUILD worker's bounded filesystem and refuse
with `may only list files in the allowed working directories`. This is not new: **ISSUE-LOG #360**
records the identical question left OPEN on wq#1807 P1-4 for the identical reason. Re-confirmed live
today and logged as **ISSUE-LOG #375**.

It is a *path* bound, not a command bound, so no synonym command reaches it and retrying is what
trips the loop breaker (#1107). **wq#1887 must be dispatched with `--add-dir /home/lumen/services`
(read-only suffices) or its restic criterion is unsatisfiable and the ticket blocks on attempt 1.**
Noted on the ticket.

**Why coverage is worth insisting on, in cost terms rather than hygiene terms.** `control.db` is not
scratch. It holds the idempotency reserve — `despag/backing/control_sqlite.py:82` `idem_reserve`,
`:113` `idem_settle` — whose entire purpose (`app.py:627-629`) is that "a retry never spawns a second
`claude -p` for work already done". Restore the box without `control.db` and every settled key is
gone, so the first wave of retries re-spawns work that already completed: a direct double-spend
against the Max pool that decisions B and C exist to protect. That is the justification line
wq#1887 should carry.

---

## B. FIRST CONSUMER = fworker — CONFIRMED, BUT THE SEAM IS CHEAPER THAN THE WORK (wq#1888)

### B.1 The call-site diff is genuinely small — ~6 lines, 2 sites

The ticket cites `FW:1219`; the function is `def run_child(...)` at **`FW:1208-1362`** (155 lines) —
1219 lands inside its docstring. Production callers are exactly two:

- `FW:1452-1453` — fire-mode (2 lines)
- `FW:1787-1790` — the main attempt path (4 lines)

Both destructure the same 6-tuple `(state, exit_code, events, killed_reason, cpu_s, pid)`. Four more
call sites are tests (`test_wq1284_spawn_hardening.py:48,76`, `test_wq992_done_watch.py:62`,
`test_loop_break_integration.py:46`).

So a `run_child_gateway` that keeps the signature and returns the same 6-tuple is a **~6-line
call-site diff**. The seam Frank picked is the right one and it is clean.

**The small diff is misleading.** All of the work is in reconstructing the *contents* of two tuple
elements — `state` (a `classifier.RunState`) and `exit_code` — from UHP events. That reconstruction
is currently impossible, for a reason in the code.

### B.2 What the translator actually keeps

`despag/uhp/translate.py:31-67` — `blocks_from_canonical()` maps one canonical stream-json frame to
its content blocks, and its docstring (`:32-34`) is explicit: kinds are
`text/reasoning/tool_use/tool_result/result`, and "anything else (system/init, a bare user prompt
echo, ...) carries no translatable content and yields nothing." For `result` it keeps exactly two
fields (`:66`): `{"text": frame.get("result"), "usage": frame.get("usage")}`.

`RunState.observe()` (`CL:155-278`) reads fourteen frame shapes. Cross-referencing the two:

| Signal | `RunState` source | Survives UHP? |
|---|---|---|
| `session_id`, `model`, `permission_mode` | `CL:161-165` (`system/init`) | **no** |
| `saw_limit`, `latest_resets_at` | `CL:202-210` (`rate_limit_event` → `rate_limit_info`) | **no** |
| `saw_limit` / `saw_model_not_found` via `api_error` | `CL:212-225` | **no** |
| `saw_limit` / `saw_model_not_found` via bare 429/404 | `CL:229-234` | **no** |
| `refusal_fallbacks`, `model_fallbacks` | `CL:236-248` | **no** |
| `api_retries`, `saw_auth_failure` | `CL:253-262` (`system/api_retry`) | **no** |
| `compact_boundaries` | `CL:264-265` | **no** |
| `result_subtype`, `result_is_error`, `terminal_reason` | `CL:267-270` | **no** |
| `total_cost_usd`, `duration_ms`, `num_turns` | `CL:276-278` | **no** |
| `turns`, `last_assistant_text` | `CL:187-199` (`assistant`) | yes |
| `usage`, `result_text` | `CL:271-275` | yes |

### B.3 Why that is fatal rather than merely lossy

Three consequences, each traceable to a line:

1. **Every gateway run misclassifies.** `classify()`'s §C1 SUCCESS test is
   `subtype == "success" and is_error is False and terminal_reason in (None, "completed")`
   (`CL:331`). With `result_subtype` permanently `None`, no gateway run can ever reach SUCCESS; all
   of them fall through to the no-result branches.
2. **LIMIT can never fire.** `CL:358-360` is `if state.saw_limit: return LIMIT`. `saw_limit` has no
   surviving source.
3. **LIMIT recovery degrades even if you patch (2).** `handle_limit` (`FW:2310-2339`) needs
   `state.latest_resets_at` (`:2311`) *and* `state.session_id` (`:2323`) to sleep-and-resume the same
   session. Missing either, it falls to `wq_release` + `EXIT_LIMIT` (`:2334-2339`) — the ticket goes
   back to the queue instead of resuming. That is a real capability regression, not a cosmetic one.

And the mid-turn limit does not reach the client in any other form: `translate.py:241-249` `fail()`
hard-codes `{"type": "harness_error", "code": "harness_error", "message": ...}`. A rate-limit stall
arrives as an opaque `harness_error` with no code and no `resets_at`.

**The entry gate is fine.** `app.py:609-623` returns 429 `lumen_max_window_exhausted` with
`detail.resets_at` and a `Retry-After` header when admission refuses *before* a spawn. Only the
mid-turn path is dark.

### B.4 The frame-gap watchdog cannot simply move to the client

`FW:1213-1215` states the contract: the frame-gap watchdog is **PRIMARY** liveness; wall-clock is
secondary. It fires at `FW:1271` on `now - last_event >= frame_gap_s`, where `last_event` is stamped
at `FW:1299` on *every* stdout line, before JSON parsing.

Over SSE that contract breaks silently. `despag/app.py:497-510` `_sse_gen()` emits `: keep-alive\n\n`
every 20 seconds of queue silence (`:504-506`), unconditionally, with no reference to whether the
backend is still producing. A client that resets its timer on bytes received will therefore **never**
declare a stall: the gateway keeps the socket warm forever. `INACTIVITY_TIMEOUT_S` defaults to 240s
(`FW:269`), so a hung backend that would today be SIGTERM'd in 4 minutes would hang indefinitely.

Either the client keys strictly on `sequence_number` advancing and ignores keep-alives, or — better —
the watchdog stays where the raw stream is, inside the gateway's runner, which is also where the kill
can actually happen.

### B.5 Also lost, and what substitutes

- `proc.pid` (`FW:1362`) and `_kill_proc_tree(proc)` (`FW:1330`) → `POST /v1/responses/{id}/cancel`
  (`app.py:712`) is the documented substitute; Phase-0 §C.2 already specifies killpg semantics behind it.
- **`exit_code` has no UHP analogue.** `FW:1335-1340` normalizes a signal death to 128+signum
  specifically so §C5/§C6's 143/137 checks see shell-convention numbers, and `CL:285-286` says that
  "absent a result frame, exit code IS the primary discriminator between KILLED(143)/OOM(137)/other".
  The response `status` is the only candidate replacement and it does not distinguish those two.
- `cpu_s` from `RUSAGE_CHILDREN` (`FW:1346-1348`) → gone; it is corroboration-only for
  LAUNCH_FAILURE/CRASH triage (`FW:1342-1345`), so this loss is acceptable.
- **The loop breaker survives.** `loop_tracker.observe(frame)` (`FW:1311-1312`) keys on tool-use
  frames, which `blocks_from_canonical` does translate (`translate.py:48-51`). Layer 2 (wq#985) is
  the one watchdog that transfers unchanged.

### B.6 The loss is material — so P2 must add this to the gateway FIRST

The decisive point in favour of doing it: **the gateway already computes the missing state and simply
does not emit it.** `despag/admission.py:170-183` `LimitObserver` feeds each turn's *canonical* frames
through the very same `classifier.RunState` fworker uses (`:171-174`), promoting a detected limit to
the account-global wall the instant it is seen. So `saw_limit` and `latest_resets_at` exist inside the
process. The client is blind only because the translator has no channel for them.

Prerequisite for wq#1888, small and precise — a vendor-namespaced sidecar, consistent with the
`lumen.budget` precedent at `app.py:390-394` (errors.md §1 vendor convention, so no future protocol
field can collide):

1. A terminal `lumen.run_state` object on the `response.completed|incomplete|failed` event carrying
   the eleven dropped fields from §B.2 — at minimum `result_subtype`, `result_is_error`,
   `terminal_reason`, `session_id`, `saw_limit`, `latest_resets_at`, `exit_code`. The gateway holds
   every one of these already.
2. A non-terminal `lumen.limit` event when `LimitObserver` flips, so a *mid-turn* limit reaches the
   client with its `resets_at` instead of as `harness_error`.
3. Frame-gap enforcement kept server-side in the runner, or a client rule that ignores `: keep-alive`.

With those three, the §B.1 mapping becomes mechanical and the canary is honest. Without them,
wq#1888's stated approach — "maps UHP events back onto the canonical frame shape the classifier
already understands" — cannot be implemented as written, because the information is not on the wire
to map back. wq#1888 already says to implement the prerequisite if small, else file it as a dep:
**it is small, and it belongs in despag, not in fworker.**

### B.7 The one-gate claim holds

Once fworker spawns via `POST /v1/responses`, `despag/admission.py:186-223` `check_admission` is the
single gate for every claude-max spawn: it applies the LIMIT wall (`:201-203`), then the governor's
`evaluate_dispatch` (`:213-216`), and fails closed on any read error (`:217-219`). `burn_rate`'s
ceiling is already shared rather than duplicated — `admission.py:270` reads
`burn_rate.FRANK_RESERVE_PCT` directly. Collapsing to one gate is a real simplification, not a
relabelling.

---

## C. FRANK-ACTIVE RESERVE BUMP — CONFIRMED, SIGNAL IS LIVE (wq#1889)

### C.1 The proposed signal exists and is populated right now

`/home/lumen/bin/wq:332-335` — `agent_sessions(session_id PRIMARY KEY, base_name, host, pid,
first_seen, last_seen)` with an index on `base_name`. `last_seen` is refreshed by `mark_alive()`
(`wq:607-614`, "One call per subcommand"), and `ping()` (`wq:616-`) extends that to read-only
subcommands too.

Live check today (`wq whoami`):

```
orch-despag4-3ca53f      base=orch-despag4     idle=3m     last=2026-09-13 03:25:08Z  live
orch-lpp-triage-eabdb9   base=orch-lpp-triage  idle=126m   last=2026-09-13 01:22:14Z  STALE
```

The orchestrator chat driving this very ticket is registered, prefixed `orch-`, and 3 minutes idle.
The signal is real, free, and already running.

### C.2 Window arithmetic is safe against the reaper

`WQ_STALE_MIN` defaults to 45 (`wq:111`) and only frees a base name. Actual deletion is
`purge_stale_sessions(ttl_min=DEFAULT_TTL_MIN)` (`wq:834-844`) with `DEFAULT_TTL_MIN = 240`
(`wq:102`), and it refuses to purge a row still holding a live lease or slock. A
`FRANK_ACTIVE_WINDOW_MIN` of 20 is comfortably inside both, so the window always evaluates against a
row that still exists — `frank_active` can never flip false because the reaper got there first.

### C.3 Correction wq#1889 needs: the reserve is applied in `pool_spend_rate`, not `allowed_concurrent`

The ticket says to apply the higher reserve "in `allowed_concurrent` (burn_rate.py:122)". The reserve
is actually read and applied inside **`pool_spend_rate()` at `dispatcher/burn_rate.py:115-117`**:

```
reserve = FRANK_RESERVE_PCT.get(pool, 0.0)
remaining_pct = max(0.0, (100.0 - float(pct)) - reserve)
```

`allowed_concurrent` begins at `:122` and only *consumes* `pool_spend_rate`'s output. Editing it
there would be editing the wrong function. Noted on the ticket.

Two properties that make the implementation cheap, both verified:

- The lookup at `:115` happens **per call**, not at import, so a value that changes between passes is
  picked up with no restart. (The module-level defaults at `:54-58` *are* import-time — that is why
  the signal must be a per-call read, not an env var.)
- The dispatcher and despag are separate processes with separate module state, so a mutated dict would
  not propagate. wq#1889 already avoids this by specifying `frank_active(now)` as a read-only query
  against the queue DB on each call — both processes then derive the same answer from the same row.
  That design is correct as written; keep it.

### C.4 Alternatives evaluated — `agent_sessions` is the cheapest honest one

- **`agent_activity`** (`wq:338-341`) — append-only `(session_id, base_name, cmd, ts)`, same DB, finer
  grain (which command, when). Strictly more information, but it is trimmed to `ACTIVITY_KEEP`
  (`wq:561-562`), so a busy fleet can age Frank's rows out of a 20-minute window while
  `agent_sessions.last_seen` stays accurate. Rejected: strictly worse for this use, at equal cost.
- **mcpw / Commander activity** — `/home/lumen/bin/` holds only `commander-blocklist.sh` and two
  one-shot `mcpw-*cutover*` scripts; there is no activity table or feed to query. Any signal would
  have to be built. Rejected: strictly more expensive, no extra fidelity.
- **`.claude` session files** — outside the BUILD worker's bounded filesystem (same class as §A.4), so
  it cannot even be evaluated from a worker, and a mtime-on-a-session-file signal would be a guess
  about a private on-disk format. Rejected on both grounds.

**Keep `agent_sessions`.** It needs no new writer, no new timer, and one indexed read.

### C.5 State the limitation honestly in the implementation

`last_seen` advances when an orch session *runs a wq command*. That is a proxy for "Frank is actively
orchestrating", not a measurement of claude.ai token burn. A chat that thinks and writes for 25
minutes without touching `wq` reads idle — a false negative. The `orch-` prefix is convention, not
enforced by the schema.

This does not sink the signal, because the existing reserve is already conservative in the same
direction: `burn_rate.py:46-48` records that the measured burn rates "include Frank's own interactive
use of the same subscription, so they overstate a worker's share (the safe direction)". The bump is a
refinement on an already-safe floor, and a false negative merely returns to today's behaviour. Say so
in the docstring so nobody later mistakes it for a usage meter.

---

## D. THE NO LIST — CONFIRMED ON ALL FIVE

**The strongest single piece of evidence is already in despag's own code.** `despag/app.py:369-379`
declares the discovery capability block with `files_input=False`, `files_output=False`,
`session_listing=False`, `harness_management=False`, `session_sharing=False`. The NO list is not a
new decision; it is the conformance surface despag already advertises to every client. Saying NO
keeps the code and the protocol honest, and each item below has an independent reason.

**uid write-wall — NO.** `HR/runner/server.py:105-113`: `HR_SESSION_UIDS=1` is mutually exclusive
with the per-session sandbox, and `:112` refuses to start unless the runner is **root**. The design
note at `:89-101` says why it exists — one runner serving many sessions needs a permission boundary
because the workspace cannot be a sandbox boundary. despag is single-tenant on one uid (`lumen`) and
deploys as `User=lumen` under decision A. Adopting the wall would mean running the gateway as root to
solve a multi-tenancy problem despag does not have, directly contradicting §A's hardening. NO is
correct on security grounds, not just scope.

**harness CRUD — NO.** HR carries `POST /v1/harnesses` (`gateway/app.py:13368`), `POST
/v1/harnesses/upload` (`:13910`), `POST /v1/harnesses/{hid}/upload` (`:13925`), `PUT` (`:14094`) and
`DELETE` (`:14109`). despag's equivalent is a **50-line static `despag/harnesses.yaml`**. Replacing a
file one operator edits with a registry plus upload storage plus validation buys nothing on a
single-operator fleet, and `harness_management=False` is already advertised (`app.py:377`).

**sharing — NO, and structurally meaningless here.** HR's sharing needs a public surface: `app.py:346`
notes the public nginx "only proxies /v1 /a /w /share", and `_share_out` returns
`url = f"/share/{token}"` (`:7897-7903`). Decision A is loopback-only with no tunnel. A share link
that resolves only on 127.0.0.1 is not a share link. The two decisions are incompatible by
construction, so NO here is forced by the yes in A.

**dsh / codex / hermes backends — NO.** Each is a distinct integration, not a config line.
`HR/runner/server.py:18-32` documents codex needing `config.toml [model_providers.*]`, hermes tailing
events from its own `state.db`, and dsh requiring `runner/dsh_driver.py` plus a bundled Node JSON-RPC
runtime (`runner/hr_dsh_server.mjs`) to re-emit `session.events` as NDJSON. Each also drags its own
credential and checkpoint-exclusion rules (`:345-349`). Phase-1's whole thesis is one canonical frame
shape; three more event sources is three more normalizers before the first consumer in decision B
even exists.

**files / artifacts deferred to Phase-3 — CONFIRMED.** `files_input`/`files_output` are already
`False` (`app.py:374-375`), and Phase-0's own RECOMMENDATION put "files/artifacts endpoints" out of
scope for Phase 1. Nothing in decision B needs them: fworker's artifacts (`attempt-N.json`,
`usage-N.json`, `episode.jsonl`) are written by fworker on the same box into the same worktree.
Deferring costs the first consumer nothing.

**Scale check on the whole list.** `HR/gateway/app.py` is 14,121 lines and `HR/runner/server.py` is
5,310, against `despag/app.py`'s **726**. The NO list is most of that difference. Phase-0 concluded
the from-scratch build was justified by exactly this; taking the NO list is what makes that
conclusion hold in Phase-2 rather than quietly reverting it.

---

## E. queue.db NOT MIGRATED IN PHASE-2 — CONFIRMED, AND THE RISK IS SHARPER THAN STATED

### E.1 The two stores are shaped differently on purpose

despag's control store is a single generic document table — `despag/backing/control_sqlite.py:50-53`,
`CREATE TABLE IF NOT EXISTS control (item_id, body, expires)` — with idempotency, leases and fences
all expressed as document ids (`lease::{sid}`, `:168`) over a key/value/TTL shape. Everything above it
talks to `typing.Protocol` interfaces, "never a concrete store" (`backing/__init__.py:5`,
Protocols at `:45-87`).

queue.db is relational and cross-table: `tasks`, `slocks`, `slocks_ro` (`wq:326-329`),
`agent_sessions` (`:332`), `agent_activity` (`:338`). Its guards are SQL *joins* across those tables
in one file — the session reaper at `wq:839-844` is a single `DELETE` with three correlated `NOT IN`
subqueries against `tasks`, `slocks` and `slocks_ro`, whose correctness depends on all of them being
visible in one transaction. That is not a document store with extra columns; it is a different data
model.

### E.2 The fence layer is feature-detected, which cuts both ways

`wq:414-420` is unusually explicit: the four lease/fence columns are **not** auto-applied like every
other column, because "the moment they exist, the claim protocol changes for every in-flight claim on
a queue shared by live sessions. That is an operator's decision with a backup behind it." They are
owned by `migrations/wq1158_lease_fence.py` and `wq` feature-detects them via `lease_fenced()`
(`:423`).

Read optimistically, that is decoupling: `wq` runs with or without them. Read accurately, it is the
opposite of reassuring — the project's own judgement is that merely *adding columns* to this table is
an operator-gated, backup-required event. A backing-store swap is categorically larger than the change
that earned that gate.

### E.3 The risk the ticket understates: the guards fail OPEN and SILENTLY

`/home/lumen/bin/lumen-wq-health:13-15` computes the dead-holder, runaway and churn guards by shelling
out to the `sqlite3` CLI with hand-written SQL against `$DB`:

```
RUNAWAY=$(sqlite3 "$DB" "select ... from tasks where status='claimed' and ... >10800;" 2>/dev/null | tr '\n' ' ')
```

Two properties matter. First, these guards are welded not merely to queue.db's *schema* but to its
being **a SQLite file addressable by the `sqlite3` binary** — they bypass `wq` entirely. Second, every
one of them ends in `2>/dev/null`. Point them at a store `sqlite3` cannot open and the command fails,
the variable is empty, and `lumen-wq-health` reports **no runaway, no dead holders, no churn** — a
clean bill of health produced by a broken query. The failure mode of migrating queue.db is not an
outage; it is a monitor that silently reports healthy while the queue rots.

The same coupling holds beyond that one script: 20+ files under `/home/lumen/bin/` reference
`queue.db` directly (`wq-reopen`, `queue-status`, `slock`, `session-handoff`, `wq-hygiene-reconcile`,
`fleet-status`, migrations, probes, tests). Each is an independent reader that a store swap would have
to be validated against.

**Verdict: do not migrate queue.db in Phase-2.** Not because the work is large — because its guard set
cannot detect its own breakage.

### E.4 Starting with despag's own stores, new writes only — CONFIRMED

This is the correct first step for the reason the risk above implies: despag's `control_sqlite` and
blob stores sit behind Protocols (`backing/__init__.py:45-87`) with exactly one consumer (despag
itself), all of it new, and no external script reads `control.db` by path. Unification there is
reversible and observable. Restricting to **new writes only** matters specifically because
`idem_settle` payloads are replayed verbatim to callers (`app.py:634-635`) — a partially migrated
idempotency table would replay wrong bodies, which is worse than replaying none.

### E.5 alerts.db as second candidate — accept, with a caveat

`alerts.db` (`/home/lumen/agents/alert-queue/alerts.db`, `bin/aq_escalate.py:56`) is genuinely second
after despag's own stores, but it is not the small step the ticket implies: at least ten files under
`/home/lumen/bin/` touch it (`aq_escalate.py`, `alert-deadman-beat.sh`, `ntfy-send`,
`lumen-load-watch`, `handoff`, `lumen-sqlutil`, `fleet-status`, `deadman-killtest.sh`, and two test
files). Fewer readers than queue.db's 20+, and its records are append-mostly alert rows rather than a
claim protocol with leases and fences, so the ordering is right. But it shares queue.db's dangerous
property — `ntfy-send` and the deadman scripts are exactly the paths that *report* failures, so a
silent break there is again invisible. Sequence it only after the despag-internal unification has
proven the migration mechanics, and give it a loud read-back check rather than a `2>/dev/null` query.

---

## RECOMMENDATION

**All five decisions stand on the evidence. Two carry corrections, one carries a hard prerequisite.**

1. **wq#1887 (deploy) — proceed.** Unit name `lumen-despag.service` is required, not stylistic
   (`systemd_desired_state.py:127`) and the ticket already has it right. Copy the
   `foreman-heartbeat` probe verbatim (`config.yaml:109-120`) including its "reads FAIL until
   enabled" comment. Justify restic coverage in double-spend terms (§A.4), not hygiene.
   **Blocker: dispatch it with `--add-dir /home/lumen/services` or its restic criterion is
   unsatisfiable (ISSUE-LOG #360, #375).**

2. **wq#1888 (fworker canary) — proceed, but the gateway work comes first and lives in despag.**
   The call-site diff is ~6 lines across `FW:1452` and `FW:1787`; the real work is the three items in
   §B.6 — a terminal `lumen.run_state` sidecar, a mid-turn `lumen.limit` event, and frame-gap
   enforcement kept server-side. All three are cheap because the gateway already computes the state
   (`admission.py:170-183`) and simply has no channel to emit it. Do them in despag and wq#1888
   becomes the mechanical mapping the ticket describes. Skip them and the canary ships a fworker that
   can never classify SUCCESS (`CL:331`), never detect LIMIT (`CL:359`), and never time out a hung
   backend (`app.py:504-506`).

3. **wq#1889 (Frank-active) — proceed as specified, with one line-reference fixed.** The reserve is
   applied at `burn_rate.py:115-117` inside `pool_spend_rate`, not in `allowed_concurrent` at `:122`.
   `agent_sessions` beats both alternatives on cost and honesty (§C.4); a 20-minute window is safe
   against both the 45-minute staleness rule and the 240-minute reaper (§C.2). Document that it
   measures orchestrator activity, not token burn (§C.5).

4. **The NO list — hold all five.** `app.py:369-379` already advertises exactly this surface, and the
   uid write-wall is additionally incompatible with §A's non-root unit (`HR/runner/server.py:112`)
   while sharing is incompatible with loopback-only (`HR/gateway/app.py:346`). The list is what keeps
   despag at 726 lines against HR's 14,121.

5. **queue.db — do not touch it in Phase-2.** Start with despag's own `control_sqlite`/blobs, new
   writes only. The decisive argument is not effort but detectability: the runaway/dead/churn guards
   shell out to `sqlite3` and swallow errors (`lumen-wq-health:13-15`), so a broken migration reports
   healthy.

**Single most important thing to build first:** the `lumen.run_state` terminal sidecar from §B.6. It
is the only item on which another ticket (wq#1888) is strictly blocked, it is small because the data
already exists in-process, and until it lands the one-gate claim in decision (2) cannot be tested —
the canary would have no way to prove the gateway path classifies identically to the direct path,
which is wq#1888's own first acceptance criterion.
