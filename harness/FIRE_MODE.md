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
any, is authoritative — this is just fworker's process exit code).

## Known gap (follow-up, not this ticket)

wq#754 (merged after this ticket started) gives every `do_attempt()` attempt a
Langfuse span (`emit_attempt_span`) and an `attempt-N.json` record
(`write_attempt_state`). `run_fire_child()` doesn't participate in either yet — a
fire-mode ticket's diagnose/apply child runs are invisible to both the Langfuse trace
view and the `_runs/wq<ID>/attempt-N.json` history that RCA on other tickets relies on.
Worth closing given how much MORE observability a production-change workflow should
have, not less — left as an explicit follow-up rather than folding an unrelated
concern into this ticket's diff.

## Env vars

`FOREMAN_PLANS_DIR`, `FOREMAN_APPROVALS_DIR`, `FOREMAN_FIRE_TOKEN_FILE` (paths, same
override convention as every other root in this harness), `FOREMAN_FIRE_POLL_S`
(default 60), `FOREMAN_FIRE_TIMEOUT_HOURS` (default 24).
