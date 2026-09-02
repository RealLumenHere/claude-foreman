# Fire-mode: fleet-fires manual-approval workflow (wq#717, AUTO-5)

`--archetype fire-response` replaces the normal retry-ladder attempt with a dedicated
diagnose → hold → apply state machine, for `[fleet-fires]` lane tickets (production
state changes: service restarts, config edits, secret rotations, repo unlocks) that
must never auto-apply without Frank reviewing the SPECIFIC proposed change first:

    python3 harness/fworker.py run --ticket <ID> --repo <path> \
      --model claude-sonnet-5 --effort high --archetype fire-response

Depends on AUTO-3 (wq#715, `dispatcher/foreman_dispatcher.py`): the dispatcher's own
fire-lane stage still applies its coarse per-ticket dispatch gate (`requires_approval`) —
`fire_approval_path(tid)` there is the SAME path as `fire_gate.approval_path(tid)` here.
This ticket adds a finer-grained, worker-driven, plan-specific approval loop on top of
it, not instead of it: the dispatcher's check is a bare `os.path.exists`, satisfied by
an empty `touch`, so a ticket can be dispatched to fire-mode before any real (JSON)
approval exists. That's fine by design — the touch only ever unblocks dispatch into the
DIAGNOSIS phase, which needs no approval at all. `fire_gate.validate_approval` still
requires the full JSON schema before the apply phase runs, so a bare touch is correctly
treated as "not yet approved" (`malformed_json`) rather than mistaken for a real
approval. **The dispatcher-level touch is not the fire-mode approval** — Frank still
has to create/replace the file with real JSON (ticket + plan_hash + token) once the plan
is ready, per the ntfy this workflow sends once diagnosis completes. wq#717's router.py
lane override (`classify_archetype`) is what makes a dispatched fleet-fires ticket
actually run under this archetype instead of the keyword rubric's default.

## The state machine

One `fworker` process drives the whole thing to a terminal outcome — `fire_gate.py`'s
`next_action()` is the pure decision function; `fworker.py`'s `run_fire_ticket()` is the
only thing that calls it and does the side-effecting work (spawn a child, ntfy, wq
lifecycle calls).

**0. Every loop iteration re-verifies first (wq#1234, extended wq#1261).** Before
   computing `next_action`, `run_fire_ticket()` calls `fire_gate.probe_cleared(ticket_detail)`.
   It prefers a `fire_probe: {"cmd": "...", "clear_when_exit": 0}` line on the ticket
   (wq#1261's machine-checkable probe schema — `fire_gate.parse_fire_probe`/
   `run_fire_probe`): the command is actually run, and clearing means its exit code
   matches `clear_when_exit`. Falls back to wq#1234's aq alert-queue row check
   (`Probe: <name>` / `aq row: #<id>` lines `aq_escalate.seed_ticket` writes) for tickets
   seeded before the cmd schema existed — that path checks whether the row is still
   `status='open'` rather than re-running anything itself. Either way, if the condition
   has cleared, the ticket auto-closes right there (`wq done`, note `"condition
   cleared: ..."`) with no diagnosis or apply child ever spawned — this is what
   wq#1154/#1160/#1161 needed: Maat/the brain model had already recovered, but the old
   loop had no way to notice and kept holding for an approval on a fire that was already
   out. A cmd-schema probe's output is written to `fire_probe_evidence.json` in the run
   dir and referenced in the `wq done` notes — the literal evidence a human (or a future
   evidence-gate replay) can check. Every re-verify is logged (`fire-mode re-verify:
   ... CLEARED|still active`), pass or fail, each iteration.

   Right after that, total wall-clock since `run_fire_ticket()` started is checked
   against `FOREMAN_FIRE_MAX_RUNTIME_S` (default 7200s / 2h as of wq#1261,
   `fire_gate.DEFAULT_MAX_RUNTIME_S`) via `fire_gate.runtime_exceeded()`. This bounds the
   WHOLE diagnose/hold/apply run, not any one child's wall-clock — once exceeded, the
   ticket `wq block`s with an `EXPIRED:`-prefixed reason carrying the current
   `next_action()` state (not `wq release`: a churning fire needs a human to look at it,
   not a silent requeue) and `run_fire_ticket()` returns `EXIT_FIRE_MAX_RUNTIME` (19).
   wq#1261 additionally wraps the WHOLE fworker session (not just this in-process check)
   in an external `timeout --signal=TERM --kill-after=60 $FOREMAN_FIRE_MAX_RUNTIME_S ...`
   at the dispatch path (`router.dispatch_cmd`, only for `--archetype fire-response`) —
   belt-and-braces so a wedged fworker (stuck in a blocking `wq()`/subprocess call before
   it ever reaches its own watchdog check) still gets torn down at the cap. No more
   13-22h runs, wedged or not.

   **Attempt fence (wq#1261).** Every terminal outcome of a `run_fire_ticket()` call is
   recorded as `_runs/wq<ID>/attempt-N.json` (`write_attempt_state`, the same convention
   `do_attempt()` uses for every other archetype) with a `fire_outcome` field. Outcomes
   that mean the ticket bounced without resolving — `expired`, `diagnosis_failed`,
   `timeout_release`, `apply_incomplete` — count against `FOREMAN_FIRE_ATTEMPT_FENCE`
   (default 2); `auto_closed` and `applied` never do. Before doing anything else,
   `run_fire_ticket()` checks `fire_fenced_attempt_count()`: at the fence, it `wq block`s
   with a `FRANK:`-prefixed reason and an urgent ntfy instead of running fire-mode a 3rd
   time — a ticket that has expired or bounced twice needs a human, not another
   automatic attempt.

   **No probe, no dispatch (wq#1261).** The dispatcher (`foreman_dispatcher.py` stage
   5b) refuses to dispatch ANY `fleet-fires`-lane ticket that carries neither the cmd
   schema nor the legacy aq-row reference — logged as `skip`/`fire_no_probe`, ntfy'd to
   Frank (24h-deduped, same discipline as the unapproved-fire ntfy). A fire you can't
   re-verify is a page, not a worker.

1. **No plan on disk yet → diagnose.** Spawns a read-only diagnosis child
   (`prompts/fire-diagnosis.md`, env `FOREMAN_FIRE_PHASE=diagnosis`) that investigates
   and writes `/home/lumen/foreman/plans/wq<ID>.md` with five required sections —
   Current State, Proposed Change, Rollback Command, Risk Assessment, Verify Command —
   then stops. `guard.sh` backs this up: Write/Edit is confined to that one path
   (`FOREMAN_FIRE_PLAN_PATH`, a hard guarantee), and a denylist blocks common mutating
   Bash verbs (`systemctl start/restart/enable`, `kill`/`pkill`, package installs,
   `mv`/`chmod`/`touch`/`tee`, in-place `sed`, mutating `curl`/`wget` — best-effort
   defense-in-depth, not a full sandbox; the plan-path restriction is the one hard
   guarantee for this phase). If the child ends without writing a plan doc, fworker
   releases the ticket for a retry.

2. **Plan exists, no valid approval yet → hold.** ntfy's Frank once with the plan
   summary and the exact JSON to create at
   `/home/lumen/foreman/approvals/wq<ID>.approved`, then polls
   (`FOREMAN_FIRE_POLL_S`, default 60s) with periodic `wq heartbeat` calls, for up to
   `FOREMAN_FIRE_TIMEOUT_HOURS` (default 24h). An approval file that shows up but fails
   validation gets its own ntfy (deduped per distinct failure code) instead of silently
   waiting out the timeout, so a typo'd approval gets fixed in minutes, not hours.

   **This intentionally does not use `wq block`.** wq's `blocked` status has no
   automatic reopen (`wq claim` refuses a blocked ticket outright; something would have
   to `wq edit --status open` it before the dispatcher could ever redispatch fworker
   for the apply phase) — a literal `wq block` here would strand the ticket on a
   mechanism this workflow has no way to satisfy automatically. The live poll loop
   inside this one `fworker` process is the actual "hold": the ticket stays claimed,
   heartbeated, and this supervisor is what notices the approval file later — no
   redispatch needed.

3. **Timeout with no valid approval → `wq release`.** Dispatcher can requeue later.

4. **Valid approval appears → apply.** Spawns the apply child (`prompts/fire-apply.md`,
   env `FOREMAN_FIRE_PHASE=apply`) with the approved plan text inlined. Its first,
   mandatory step is to re-verify live state still matches the plan's "Current State"
   section — state may have drifted in the (up to 24h) gap between diagnosis and
   approval. If it's drifted, the child writes a drift marker
   (`/home/lumen/foreman/plans/wq<ID>.drift`) instead of applying anything; fworker
   clears the stale plan/approval/drift files and restarts from diagnosis. Otherwise it
   applies the change, runs the verify command, and calls `wq done`/`wq block` itself —
   the same self-reporting contract as the standard `prompts/worker.md` flow.

## fire_probe schema (wq#1261)

One line in the ticket's `detail`, JSON:

    fire_probe: {"cmd": "curl -sf http://localhost:9200/_health", "clear_when_exit": 0}

`cmd` runs through the shell with a 60s timeout (`fire_gate.DEFAULT_PROBE_TIMEOUT_S`);
the ticket is considered cleared iff its exit code equals `clear_when_exit`. Fails
closed on a timeout, a spawn failure, or a malformed line (not valid JSON, not an
object, `cmd` not a non-empty string, `clear_when_exit` not an int) — "can't check" is
never mistaken for "cleared", same contract as every other fail-closed check in this
module. `aq_escalate.py`-seeded tickets (wq#1234's `Probe: <name>` / `aq row: #<id>`
lines) keep working unchanged as a fallback; a hand-filed drill ticket with no aq row at
all can now carry its own re-verify condition directly. `fire_gate.probe_cleared()` is
what `run_fire_ticket()` actually calls each iteration — it tries the cmd schema first,
then the aq-row fallback.

## Approval file format

JSON, at `/home/lumen/foreman/approvals/wq<ID>.approved` (`fire_gate.approval_path`):

    {
      "ticket": 717,
      "plan_hash": "<sha256 of the current plan doc's exact bytes>",
      "token": "<contents of FOREMAN_FIRE_TOKEN_FILE>",
      "approved_by": "frank",
      "approved_at": "2026-08-22T12:00:00Z"
    }

`ticket`, `plan_hash`, and `token` are the only fields `fire_gate.py` checks (`approved_by`/
`approved_at` are free-form, for a human reading the file later — not validated). Frank
computes `plan_hash` from the plan doc fworker's ntfy message already includes it
precomputed, so in practice this is copy-paste, not a manual hash. The token lives at
`FOREMAN_FIRE_TOKEN_FILE` (default `/home/lumen/.secrets/foreman-fire-approval.token`) —
a plain-text 0600 secret, same convention as `OAUTH_TOKEN_FILE`/`LITELLM_KEY_FILE`.

## Guardrails (ticket's MUST list) and how each is met

- **MUST NEVER approve itself.** Structural, not policy: `FOREMAN_FIRE_TOKEN_FILE` lives
  under `/home/lumen/.secrets`, a path `guard.sh` denies outright to every worker's
  Bash/Read/Edit/Write/Grep/Glob tool calls (this ticket closed the Grep/Glob gap — see
  guard.sh's `Read|Edit|Write|Grep|Glob` case). The sandboxed child fire-mode exists to
  constrain cannot read the secret it would need to forge an approval. Only fworker's
  own supervisor process — outside the guard, same trust boundary as `OAUTH_TOKEN_FILE`
  (wq#616) — ever opens that path, and only to compare (never to write).
- **MUST validate approval file integrity.** `fire_gate.load_approval`/`validate_approval`
  fail closed on anything not a well-formed JSON object with all three required fields,
  the matching ticket id, and the matching token — see failure modes below.
- **MUST re-verify current state matches plan before applying.** Two layers: (1) the
  apply-phase child's mandatory re-verify step (above) catches live-state drift; (2)
  `validate_approval`'s `plan_hash` check catches plan-content drift — if the plan doc
  itself was rewritten (a second diagnosis attempt, a manual edit) after Frank approved
  the old text, the hash no longer matches and the approval is rejected as `plan_drift`
  before the apply child ever spawns.

## Failure modes (`fire_gate.validate_approval`'s `code` values)

All of these are fail-closed: `next_action()` treats every one of them as "not yet
approved" (`hold`, or `timeout_release` once the clock runs out) — never as an error
that halts the ticket outright, and never as an approval.

| code                  | meaning                                                          |
|-----------------------|-------------------------------------------------------------------|
| `missing`             | no approval file yet — the normal hold state, not an error       |
| `malformed_json`      | approval file exists but isn't valid JSON                        |
| `missing_fields`      | JSON parsed but lacks `ticket`/`plan_hash`/`token`                |
| `ticket_mismatch`     | approval names a different ticket — no copy-pasting one ticket's approval onto another |
| `token_file_missing`  | `FOREMAN_FIRE_TOKEN_FILE` itself is absent (mis-provisioned host) |
| `bad_token`           | token doesn't match `FOREMAN_FIRE_TOKEN_FILE`                     |
| `plan_missing`        | no plan on disk to compare the approval's `plan_hash` against    |
| `plan_drift`          | `plan_hash` no longer matches the plan currently on disk          |

## Exit codes (fire-mode only)

`16` timeout release, `17` diagnosis produced no plan doc, `18` apply phase ran but
didn't reach SUCCESS/DEGRADED_SUCCESS (the child's own `wq block`/`wq release` call, if
any, is authoritative — this is just fworker's process exit code). `19` max-runtime
exceeded — blocked for a human (wq#1234). `20` attempt fence tripped — escalated to
FRANK without running fire-mode again (wq#1261). `0` (`EXIT_OK`) also covers the
re-verify auto-close path (condition cleared, `wq done` called directly by the
supervisor rather than by a child).

## Known gap, closed (wq#1261)

wq#754 gives every `do_attempt()` attempt a Langfuse span (`emit_attempt_span`) and an
`attempt-N.json` record (`write_attempt_state`); `run_fire_child()` still doesn't emit a
Langfuse span, but `run_fire_ticket()` now writes one `attempt-N.json` per terminal
outcome (with a `fire_outcome` field — see the attempt-fence description above), so fire
tickets are no longer invisible to the `_runs/wq<ID>/attempt-N.json` history RCA on other
tickets relies on, and `dispatcher/self_heal.py`'s `attempt_count()` sees them too. The
Langfuse span gap is still open — worth its own follow-up, not folded in here.

## Env vars

`FOREMAN_PLANS_DIR`, `FOREMAN_APPROVALS_DIR`, `FOREMAN_FIRE_TOKEN_FILE` (paths, same
override convention as every other root in this harness), `FOREMAN_FIRE_POLL_S`
(default 60), `FOREMAN_FIRE_TIMEOUT_HOURS` (default 24), `FOREMAN_FIRE_AQ_DB` (path to
the alert-queue sqlite db the aq-row re-verify fallback reads, default
`/home/lumen/agents/alert-queue/alerts.db`), `FOREMAN_FIRE_MAX_RUNTIME_S` (total
wall-clock cap, default 7200 / 2h as of wq#1261 — read by both `fire_gate.py`'s
in-process watchdog and `router.py`'s external `timeout` wrap, so they always agree),
`FOREMAN_FIRE_MAX_RUNTIME_KILL_AFTER_S` (the external wrap's `--kill-after`, default 60),
`FOREMAN_FIRE_ATTEMPT_FENCE` (expired/rework attempts before escalating to FRANK instead
of redispatching, default 2).
