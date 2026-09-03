# Orchestrator operability: why long unattended sessions lose time to tooling (wq#1369)

## 0. Source and scope

Evidence: `kb/ISSUE-LOG.md` "2026-09-02 — ORCHESTRATOR OPERABILITY FRICTION" (6 incidents from
one long orch-opus48 session, row before this section). This doc root-causes each incident against
the live tree (`/home/lumen/bin/wq`, `foreman/dispatcher/*.py`) and ranks fixes by
(time saved per session × recurrence). No code changes in this ticket — design only, per the
ticket's own instruction ("reviewed before code").

Cross-refs, checked so this doc does not double-recommend work already filed:
- **wq#1330** (done, PR#250): `failure.json` schema + mechanical triage — this is TICKET-level
  observability (why did *this ticket* fail). Item 3 below is PASS-level (why did the *dispatcher*
  spawn nothing this pass) — different layer, already noted as non-overlapping in wq#1330's own
  scope.
- **wq#1334** (done, PR#244): UID split for fworker sandboxing — orthogonal (process boundary, not
  session lifecycle or dispatch observability). No overlap.
- **wq#1368** (claimed, in progress as of this writing): dispatcher pass-crash hardening
  (unhandled `ManifestError` from a bad archetype kills the whole pass) + the producer that emitted
  the bad archetype. **This doc's item 3 finding is upstream of wq#1368**: the reason nobody was
  paged about that crash is not "there's no watchdog" — a watchdog exists (wq#825) — it's that the
  watchdog's classifier doesn't recognize an `_do_pass`-loop crash as unhealthy (§3.2). wq#1368
  fixes the crash; this doc's slice-1 fix (§3.4) fixes the blind spot in the thing that should have
  paged Frank about crashes like it, including future ones wq#1368 doesn't anticipate. Do not
  re-file the crash-hardening half — that's wq#1368's job.

---

## 1. Session lifecycle — budget/retire firing mid-work

### 1.1 What happens, verified live

`bin/wq:2170-2175`:
```
init()
a = p.parse_args()
if not getattr(a,"cmd",None): p.print_help(); sys.exit(0)
enforce_retirement(a)
a.f(a)
```
`enforce_retirement()` (`bin/wq:532-607`) runs before **every** subcommand dispatch — including
`edit`, `claim`, `note`, `done`. Trip conditions (`bin/wq:581-588`):
```
if age is not None and age > SESSION_MAX_AGE_MIN:      # default 360m, wq:114
    trip = ("auto-age", ...)
elif done_n >= SESSION_MAX_TICKETS:                     # default 8, wq:115
    trip = ("auto-tickets", ...)
```
When tripped, `enforce_retirement` atomically releases the session's live claims/slocks
(`release_session_holdings`, `bin/wq:496-510`), marks the session retired, and `sys.exit(3)` —
**before** `a.f(a)` runs. The subcommand the orchestrator actually invoked (e.g. `wq edit 1353
--detail "..."`) never executes at all. This is exactly what the ISSUE-LOG row describes: the
`--detail` edit content is simply lost, twice, because the write that would have applied it was
refused at the gate, not because a write-in-progress was interrupted mid-flush.

Two independent triggers, same effect:
- **auto-tickets** (`bin/wq:586-588`): 8 closed claims is a *budget*, not a workload signal. An
  orchestrator doing a sweep of small closures (the exact shape of the incident's session) hits it
  routinely — it counts `status='done'` claims under the session id with no decay and no
  distinction between "8 five-minute closures" and "8 two-hour builds."
- **auto-age** (`bin/wq:582-585`): 360 minutes (6h) wall clock since `first_seen`, regardless of
  activity level. An orchestrator that is actively coordinating (not idle — `bin/wq:52-53`
  explicitly says this is deliberately *not* the 45-minute idle threshold) still trips this at the
  6h mark.

Both are env-tunable (`WQ_SESSION_MAX_AGE`, `WQ_SESSION_MAX_TICKETS`, `bin/wq:114-115`) — that is
the currently-available workaround (raise the env for a marathon session), and it is exactly what
`bin/wq:54` invites: *"A legitimate marathon session raises the env; it does not edit shared
tooling."* Nothing currently *identifies* an orchestrator session as such, so nothing raises the
env automatically — a human (or the orchestrator itself, out of band) has to know to do it before
the session starts.

### 1.2 Why this is a design gap, not a bug

The budgets exist for a real reason (`bin/wq:33-35`, `50-54`): they are a backstop against sessions
that die without deregistering cleanly and silently accumulate claims/slocks forever. Removing
them for *all* sessions reopens that hole. The gap is that **the mechanism has exactly one class**
(any session, same budget) when the fleet actually has two: short-lived BUILD workers (one ticket,
then exit — the class in this repo's own worker prompt) and long-lived ORCH sessions (dispatch,
triage, coordinate for hours, holding many sequential claims by design).

### 1.3 Fix options, ranked

1. **Orchestrator-class identity with an exempt/higher budget (recommended, smallest slice).**
   `wq whoami --register` (or equivalent) already takes a base name; a base name matching a
   recognized orchestrator pattern (`orch*`, `foreman*`, `forch*` — the same pattern
   `cmd_decide`'s docstring at `bin/wq:1732-1734` already describes as the *cultural* convention
   for orchestrator sessions) could set `SESSION_MAX_TICKETS`/`SESSION_MAX_AGE` to a
   per-class value read from a small config (or simply multiply by a constant, e.g. 6x/3x) at
   registration time, stored on the `agent_sessions` row so `enforce_retirement` reads the
   session's own budget instead of the global env. This reuses an identity convention that
   already exists elsewhere in the same file rather than inventing a new one.
2. **Graceful retire that drains instead of severing.** Harder: would need `enforce_retirement` to
   distinguish "about to run a mutating subcommand" from "safe checkpoint," which means either a
   two-phase protocol (announce intent to retire, orchestrator finishes its current call, THEN
   retires on the *next* call) or deferring the trip check to *after* `a.f(a)` for the call that
   crosses the threshold. The latter is a one-line change (`bin/wq:2174-2175`: run `a.f(a)` first,
   check retirement after) but changes the safety property described at `bin/wq:39-40` ("EVERY
   subcommand ... refuses ... when retired") from "refuses before acting" to "acts once more, then
   refuses" — worth doing but is a behavior change to a documented safety invariant, not a
   config tweak, so it should not ship in the same slice as option 1.
3. **Transparent auto-re-register+resume.** Highest effort, most fragile: requires the *caller*
   (the harness spawning `claude -p` or the interactive session) to notice the exit-3 stop banner,
   re-register under a fresh session id, and replay the exact call that was refused — replaying a
   `done`/`edit` call safely requires idempotency the current CLI doesn't guarantee (re-running
   `wq done` on a ticket that gate-refused already trips the wq#1116 already-done refusal at
   `bin/wq:1622-1634` if the first attempt partially landed some other way). Not recommended as the
   first slice.

**Recommendation: build option 1.** It's a small, additive change (new column or lookup, read at
`enforce_retirement` time), doesn't touch the safety invariant, and directly removes the
highest-recurrence item in the log (fired 3 times in one session).

---

## 2. Command output reliability (Commander/DC truncation)

### 2.1 Root cause, already documented

`reports/416_dc_handle_store_report.md` (wq#416, 2026-08-08) is the prior investigation into this
exact message class. The handle-store patch to `desktop-commander-mcp`'s `terminal-manager.js`
gives cross-call output recovery with an explicit, accepted limitation stated in its own drill
log (§2, drill 2): *"process ended while DC recycled; output recovered from disk (up to last
flush)"* — capture is **up to the last periodic flush** (report line 12: "1s flush" interval,
5MB/process cap), by design. When the DC/mcpw gateway process is recycled (restart, OOM, flap) in
the window between a burst of output and the next flush, everything after the last flush for that
call is gone — not corrupted, just never persisted. This is not new breakage; it is the known,
accepted shape of a fail-open design (report line 43-47: a hard version pin was explicitly rejected
in favor of a drift-tripwire, same tradeoff logic applies here — recovery-with-a-gap beats no
recovery).

The report's own conclusion (line 30) already names the mitigation: *"Capture is up-to-last-flush
per design; **lumen-run remains the rule for long jobs**."* `bin/lumen-run` (`start`/`status`/
`log`/`wait`) launches a command as a detached systemd transient unit and reads its output back via
`journalctl -u <unit>` (`bin/lumen-run:88,98,105`) — output capture is then owned by journald, not
by the DC gateway's in-memory buffer-and-flush loop, so a DC/mcpw restart mid-job cannot truncate
it at all.

### 2.2 The actual gap

The incident log describes *synchronous, single-call* commands with large output (a long
`grep`/pipeline) getting truncated — not long-running background jobs. `lumen-run` is built for the
background-job case (start, poll, fetch log later) and is heavier to invoke for a one-shot
foreground command than just running it. The orchestrator's own workaround — write to a file, then
read the file back — sidesteps the gateway's in-band output channel entirely, which is the same
principle `lumen-run` uses (durable store outside the recycle boundary), just done by hand, per
incident, after the fact.

### 2.3 Fix, ranked

1. **Document the pattern, make it the default reflex (smallest slice).** Add explicit guidance
   (CLAUDE.md operational note, or a one-line addition to `reports/416_dc_handle_store_report.md`'s
   successor doc) that for any command *expected* to produce output beyond a small bound, the
   orchestrator should default to `cmd > /tmp/out.txt 2>&1; wc -l /tmp/out.txt` (or equivalent)
   rather than relying on the DC channel, exactly as the report already recommends for long-running
   jobs. Zero engineering cost — the gap is that the guidance from the wq#416 report never made it
   into anything the orchestrator reads by default (it's a reports/ postmortem, not an operational
   doc).
2. **Raise the flush cadence or make flush-on-large-write immediate** (in `terminal-manager.js`'s
   patch) so the loss window shrinks from "up to 1s of output" to "up to one write() call." Real
   fix for the underlying truncation, but this is third-party-package patch territory
   (`dc-patch-tripwire` already exists specifically because these patches get silently wiped on
   `npm` bumps — report lines 32-40) — a new patch is a new thing for that tripwire to defend, and
   the *host* of that code is outside this repo's allowed dirs from a build worktree, so it needs
   its own ticket scoped to wherever `/home/lumen/uplift/dc-handle-store/` and the DC install live.
3. **A thin wrapper tool that always redirects to a temp file and returns the path** (formalizing
   option 1 as a callable instead of a convention) — marginal value over option 1 once the
   documented reflex exists; only worth it if the manual workaround keeps recurring after option 1
   ships.

**Recommendation: ship option 1 first** (pure documentation, addresses "several round-trips" cost
directly, zero risk), file option 2 as its own ops-lane ticket against the DC patch set if
recurrence continues after option 1.

---

## 3. Dispatch-stall observability

### 3.1 A watchdog already exists — and is live

`foreman/dispatcher/dispatch_health.py` (wq#825) is wired to `systemd/lumen-foreman-health.timer`
+ `.service` (confirmed live: `systemctl is-enabled/is-active` both return
`enabled`/`active`, `ActiveEnterTimestamp=2026-08-31 12:15:02Z` — running since before the
incident; `runtime/health_state.json` shows `last_alert_ts: 2026-09-03T01:26:19+00:00`, so it *has*
fired at least once around the incident window). It reads the dispatcher's own JSON decision log
and classifies each pass (`classify_passes`, `dispatch_health.py:106-175`) as healthy/unhealthy/
skip, alarming via `ntfy` when the last N (default 3, `DEFAULT_THRESHOLD`, line 50) scored passes
are all unhealthy.

So item 3 as literally stated in the ticket ("timer fired but spawned 0 for K consecutive passes
with ready>0 → alert") **is already built**. The incident happening anyway means the existing
mechanism has real gaps — found two, both confirmed by reading the classifier against the crash
path that actually fired:

### 3.2 Gap A — a pass that crashes mid-loop is invisible to the watchdog, not just unpaged

`foreman/dispatcher/foreman_dispatcher.py:890-899`:
```python
try:
    log.emit("pass_start", ...)
    summary = _do_pass(cfg, log, load_state(), dry_run)
    log.emit("pass_end", **summary)
except Exception as e:
    log.emit("error", stage="pass", reason=f"unhandled: {e!r}")
    ntfy(f"dispatcher: pass {pass_id} crashed: {e!r}")
    rc = 1
```
This is exactly the wq#1368 crash shape (`ManifestError` from a bad-archetype ticket, raised deep
inside `_do_pass`'s per-candidate loop). It correctly logs `error stage=pass` and fires an
immediate one-off `ntfy` — so Frank *is* paged the instant it happens, no threshold needed. But
`dispatch_health.py`'s classifier only recognizes one error shape as unhealthy
(`dispatch_health.py:132`):
```python
if event == "error" and ev.get("stage") == "config":
    has_config_error = True
```
`stage == "pass"` never sets `has_config_error`, and the crashed pass also has no `pass_end`
(the crash happens before line 895 in `foreman_dispatcher.py`), so it falls through to the final
branch (`dispatch_health.py:171-173`):
```python
else:
    # No pass_end, no config error — possibly incomplete log line, skip
    results.append((pid, ts, "skip", "incomplete pass data"))
```
A `_do_pass`-loop crash is classified `skip` — **excluded from the scored window entirely**
(`check_health`, line 186: `scored = [... if v != "skip"]`). It never counts toward the
3-consecutive-unhealthy streak. If `foreman_dispatcher.py`'s own inline `ntfy` (line 898) is the
only thing that ever pages for this crash class, then a *silenced* or *rate-limited* notify path,
or a crash that happens to occur in a context where that single `ntfy` call itself fails, has zero
backstop — the one thing built specifically to backstop dispatcher failures doesn't recognize this
failure shape as a failure. This is the header-level finding this doc adds beyond what wq#1368 already knows: fixing the crash (wq#1368) removes this specific trigger, but the blind spot in the
classifier survives for the next unanticipated crash shape.

### 3.3 Gap B — the watchdog pages a human channel, not the orchestrator session

Even where the watchdog *does* correctly fire (Gap A doesn't apply — e.g. the stale-preflight case,
which does produce `pass_end` with `spawned=0, candidates>0` and should be caught by
`dispatch_health.py:158-162` within `DEFAULT_THRESHOLD=3` passes), the alarm is an `ntfy` push
(`dispatch_health.py:60-65`) to `lumen-alerts` — a notification channel Frank watches, not a
channel the orchestrator's own session reads. An autonomous orchestrator with no standing
instruction to poll `ntfy` history or a dashboard has no way to *learn* the alarm fired, even when
it fired correctly and promptly. The ISSUE-LOG's "WANT: alert" is really "WANT: something *this
session* can see," which a human-facing push notification structurally isn't, however well-tuned
its threshold is.

### 3.4 Fix, ranked

1. **Close Gap A (highest leverage, smallest slice): widen the classifier.** In
   `classify_passes`, treat `event == "error"` with **any** `stage` (not just `"config"`) and no
   following `pass_end` as unhealthy, with the stage recorded in the reason string (mirrors what
   `foreman_dispatcher.py:897` already logs — the data is there, the classifier just isn't reading
   it). This makes the *existing* wq#825 watchdog catch every future unhandled-exception crash
   class, not just config-load ones — durable beyond whatever wq#1368 fixes for this one instance.
   Small, isolated, well-covered by `dispatcher/tests/test_dispatcher_health.py` already existing
   as a pattern to extend.
2. **Close Gap B: give the orchestrator a session-visible read, not just Frank's phone.** Cheapest
   version: a `wq`-adjacent read command (or reuse of `dispatch_health.py check --dry-run`'s
   existing JSON-ish output at line 196) that the orchestrator runs as a periodic self-check
   (e.g., once per N dispatch waits) instead of relying on being pushed to. This turns a
   push-only signal into something a session can pull on its own schedule — no new alerting
   infrastructure, just exposing the existing `check_health()` result as something the
   orchestrator's own loop consults.
3. **Merge `dispatch_health.py` and `dispatcher_health.py`.** Two files both claim to be "wq#825's
   watchdog" (`dispatcher_health.py:2-26` docstring vs. `dispatch_health.py:2-28`); only
   `dispatch_health.py` is wired to the live systemd unit (`systemd/lumen-foreman-health.service:14`
   calls `dispatch_health.py check`). `dispatcher_health.py` (220 lines, its own
   `CONSECUTIVE_THRESHOLD` env var, its own docstring citing the same wq#825 problem) appears to be
   a superseded draft never deleted — worth a follow-up ticket to confirm and remove the dead one,
   since two files with near-identical purpose and different thresholds (2 vs. 3) is exactly the
   kind of drift that causes a future "which one is authoritative" mistake. Not urgent, but cheap
   and low-risk to file.

**Recommendation:** #1 first (fixes the actual silent-crash class, reuses all existing
infrastructure, a handful of lines in one function), #2 next (turns "Frank was paged" into
"the orchestrator itself can know"), #3 as a cheap follow-up ticket, not blocking.

---

## 4. Closure ergonomics — the blocked-ticket dance and the evidence-gate rejection

### 4.1 The blocked-ticket-claimed-by-dead-worker dance, traced end to end

Verified by reading the actual state transitions, not just the log's summary:

- `wq retire <dead-session>` → `release_session_holdings` (`bin/wq:496-510`) only reverts tickets
  with `status='claimed'`. A **blocked** ticket is untouched by retire — `cmd_block`'s `_finish`
  call (`bin/wq:1656-1661`) clears `lease_expires` but explicitly does **not** clear `claimed_by`:
  ```python
  elif new_status == 'blocked':
      cur = c.execute(f"""UPDATE tasks SET status='blocked', notes=?, block_reason=?,
                   blocked_at=?, lease_expires=NULL{clr} WHERE id=?{fcl}""", ...)
  ```
  So after retire, the blocked ticket still shows `claimed_by=<dead session>`.
- `wq edit <id> --status open` is the only way to move it, and `cmd_edit` **never calls
  `owner_ok`** (confirmed: `owner_ok` is only referenced from `_finish`, `bin/wq:1615`, and
  `cmd_heartbeat`, `bin/wq:1433` — not from `cmd_edit`) — so this step bypasses the ownership check
  entirely, but it *also* doesn't clear `claimed_by` (the column isn't in `cmd_edit`'s field list,
  `bin/wq:1919-1923`). Net effect: ticket is now `status='open'` with a stale `claimed_by` still
  set.
- `wq claim --id <id>` is needed next because a *direct* `wq done`/`block`/`release` on the still
  claimed-by-someone-else ticket would hit `owner_ok`'s unconditional refusal for a **registered**
  caller (`bin/wq:711-712`, case 3: *"caller registered, holder is someone else → REFUSE. A
  registered session always knows its identity and has no excuse."* — deliberately unconditional,
  wq#1116, to close the two-orchestrator collision hole). `claim --id`'s own reclaim-on-claim
  housekeeping (mentioned at `bin/wq:2019-2022`) picks up the now-open, lease-cleared ticket
  cleanly.
- `wq done <id> --notes ...` finally closes it, now that `claimed_by == caller`.

Four calls (`retire`, `edit --status open`, `claim --id`, `done`) to close one dead-claimed blocked
ticket, and `reap` — the tool built specifically for "sweep dangling claims" (`bin/wq:2017-2030``)
— doesn't help here either: its query (`bin/wq:2037-2039`) is
`WHERE (status='claimed' OR (status='open' AND claimed_by IS NOT NULL)) AND lease_expires < ?`,
which never matches `status='blocked'`.

### 4.2 Why this is a deliberate consequence, not an oversight

`owner_ok`'s unconditional case-3 refusal exists *because* of wq#1116 (two unregistered
orchestrators colliding on one ticket, one silently overwrote the other's close). Loosening it to
"allow a registered caller through if the holder looks dead" reopens exactly that hole — liveness
detection is a race, and wq#1116's whole point was that a race here is how ownership got silently
stomped. So the fix is not to weaken `owner_ok`; it's to give the retire path (which is explicitly
a deliberate, session-scoped, atomic operation already) the *scope* to cover blocked tickets too.

### 4.3 Fix, ranked

1. **Extend `release_session_holdings` to also revert `status='blocked'` tickets claimed by the
   retiring session (smallest, most direct slice).** One additional `UPDATE` (or broaden the
   existing `WHERE status='claimed'` to `WHERE status IN ('claimed','blocked')`) in
   `bin/wq:496-510`. This collapses the dance from 4 calls to 1 (`wq retire <dead-session>` alone
   reopens the ticket, ready for a normal `claim --id` + `done`), stays inside retire's existing
   atomicity and ownership semantics (retire already requires no special ownership check — it acts
   on a *session*, not on someone else's live claim), and needs no new verb.
2. **Extend `reap` to also sweep `status='blocked'` with an expired-or-null lease.** Covers the
   case where nobody thinks to retire the dead session explicitly (reap runs on its own schedule,
   `bin/wq:2029`: "run on its own schedule via lumen-run/cron"). Slightly broader blast radius
   (reap is lane-wide and automatic, not operator-invoked) — do this *after* #1, as a belt-and-
   suspenders follow-up, not instead of it.
3. **A dedicated lighter verb (e.g. `wq unblock <id>`)** that does retire-scope-for-one-ticket +
   reopen atomically without touching the rest of a session's holdings. Only worth building if #1
   turns out to be too broad in practice (i.e., retiring the whole session when only one blocked
   ticket needs freeing is sometimes wrong) — no evidence yet that it is, so don't build ahead of
   that need.

### 4.4 The `wq done` evidence-gate `path:line` rejection

Root cause, traced in `find_artifact` (`bin/wq:1470-1489`):
```python
for raw in notes.split():
    tok = raw.strip(".,;:()[]\"'")
    ...
    for cand in _artifact_path_candidates(tok):
        if os.path.exists(cand):
            return True, "path", cand
```
`str.strip(charset)` only trims characters from the *ends* of the string that are members of the
charset — for a token like `research/ORCHESTRATOR_OPERABILITY.md:88-104`, the token starts with
`r` and ends with `4`, neither in the strip set, so **nothing is stripped** and the literal string
(including the `:88-104` suffix) is passed to `os.path.exists()`, which is false. The gate then
refuses with the message quoted in the log ("needs bare existing path"). This is a narrow,
mechanical gap: the function already treats a bare path as valid evidence
(`_artifact_path_candidates`, `bin/wq:1462-1468`) but has no notion of "path, then a line-locator
suffix" — the exact shape this ticket's own evidence gate (`harness/fworker.py verify-evidence`,
a different mechanism, see the ticket preamble) explicitly wants and validates against a real diff.

**Fix (small, isolated):** before the `os.path.exists` check, strip an optional trailing
`:\d+(-\d+)?` (and optionally `:L?\d+` for common `path:L88` shapes) from the candidate token and
re-test existence on the stripped form, falling back to the literal token if stripping yields
nothing that exists. One regex, one branch, in `find_artifact` — no schema change, no new CLI flag,
directly removes a "rejected until reworded" round-trip that recurs across the fleet's own
`file:line` citation convention (used throughout this very doc, and required verbatim by this
ticket's own `evidence.json` contract).

**Recommendation:** ship 4.1/#1 and 4.4 together — both are single-function, low-risk, high-
recurrence fixes with no interaction between them. File 4.1/#2 and 4.3 as-needed follow-ups, not
in the first slice.

---

## 5. Summary ranking (time saved per session × recurrence, smallest-highest-leverage first)

| # | Fix | Item | Effort | Recurrence in the one logged session | File(s) |
|---|-----|------|--------|---------------------------------------|---------|
| 1 | Widen `classify_passes` to any `error` stage, not just `config` | 3 Gap A | ~10 lines, 1 function | Silent — undercounts every non-config crash, incl. wq#1368's class | `foreman/dispatcher/dispatch_health.py` |
| 2 | `find_artifact`: strip trailing `:line`/`:line-line` before path-exists check | 4.4 | ~5 lines, 1 function | 1x logged, "recurring" per log's own word | `bin/wq` |
| 3 | `release_session_holdings`: also revert `status='blocked'` claims | 4.1 | ~1 query broadened | 1x logged (multi-step each time) | `bin/wq` |
| 4 | Orchestrator-class session budget (exempt/higher `SESSION_MAX_*`) | 1 | New column/lookup at registration + read in `enforce_retirement` | 3x in one session | `bin/wq` |
| 5 | Document write-to-file-first reflex for known-large synchronous output | 2 | Docs only | "several round-trips" per log | CLAUDE.md / ops doc |
| 6 | Orchestrator self-check pull on `dispatch_health.check_health()` | 3 Gap B | Small — expose existing function's result | Structural (was always true, just unobserved) | `foreman/dispatcher/dispatch_health.py` + orchestrator loop convention |
| 7 | `reap` also sweeps stale-lease `status='blocked'` | 4.1 (belt-and-suspenders) | ~1 query | Backstop, not primary path | `bin/wq` |
| 8 | Delete/merge dead `dispatcher_health.py` vs. live `dispatch_health.py` | 3 (hygiene) | Confirm-then-delete | Zero direct cost yet; risk of future confusion | `foreman/dispatcher/` |
| 9 | Real DC/terminal-manager flush-cadence fix | 2 (deeper) | Third-party patch, own tripwire coverage | Only if #5 doesn't fully absorb the cost | outside this repo |

Items 1-4 are the recommended first build slice: each is a single, isolated, low-risk change in a
file already read in this doc, none touch a documented safety invariant, and together they remove
every incident in the log except the DC truncation class (which item 5, pure documentation,
addresses at effectively zero cost). Ticket the build as a small follow-up scoped to exactly these
four changes plus the doc addition, deps on nothing (wq#1368 is independent — see §0).
