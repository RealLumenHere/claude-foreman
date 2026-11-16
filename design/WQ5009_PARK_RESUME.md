# wq#5009 — park-and-resume on Frank-gated decisions

Pattern source: `scratch/research/extract_agent_autoloop.md` (wq#4974,
KKKKhazix/agent-autoloop). Patterns ported: park-and-resume, the decision-card protocol,
answer routing, the hop-count breaker. Not ported: the framework, its hub/HTTP API, its
channel plugins, merge-by-subject (a queue change, separate ticket).

## Defect class

A worker that reaches a decision only Frank can make had two bad exits:

| exit | cost |
|---|---|
| `wq block` / die | the session and everything it had worked out is gone; when Frank answers, a new worker starts cold. The frank_gated queue is a graveyard (44 tickets). |
| wait in-session | a concurrency slot (3 total) and tokens burn until the wall clock kills it |

Class: **a human wait is modelled as a running process**. Fix: a wait is state on disk.

## Flow

    worker    final message = ```foreman-park {"ask": {...}}``` + EXIT_SIGNAL: false + PARKED
    fworker   park_round(): _runs/wq<id>/park.json  <- session id, worktree, attempt, card
              wq release ; wq edit --status frank_gated ; wq decide (card -> pending_review)
              ntfy the card ; exit EXIT_PARKED (27) — uncharged, refill kicked
    ...       no process, no tokens, one small file; survives reboot
    Frank     ssh lumen 'python3 /home/lumen/foreman/harness/park.py answer <id> "<choice>"'
              (plain `wq answer` works too; the next sweep picks it up)
    sweep     first answer wins -> card.choice/answer ; wq review accept -> ticket open
    dispatch  any dispatcher claims the open ticket (auto-dispatch puts it first)
    fworker   cmd_run: peek_resume() -> do_attempt(resume_session=<parked sid>,
              prompt = "DECISION RECEIVED … Frank chose: …") ; mark_resumed() after gates

The wq states are the existing wq#662 decision cycle (`frank_gated -> pending_review ->
open`); nothing in wq changed. What is new is that a session is attached to the cycle.

## Round state machine (`park.json: state`)

    RUNNING -> PARKED -> ANSWERED -> RESUMED -> RUNNING | PARKED (next round)
               PARKED | ANSWERED | RESUMED -> CANCELLED   (ticket moved/closed by hand)

Illegal transitions raise. `hops` counts parks per ticket; past `MAX_HOPS` (8) a card is
not honoured and the exit takes the ordinary path — the hop-count breaker for a worker
that only ever asks.

Mapping to agent-autoloop's round states: Queued = wq `open`; Claimed/Started = wq
`claimed` + fworker unit; Done = `done`; Failed-retry = the existing attempt ladder +
wq#5007 breaker; parked = `pending_review` + `park.json`.

## Decision card

    {"ask": {"title": str<=120, "text": str<=2000,
             "options": [<=6 distinct strings <=200] (optional),
             "recommend": one of options (optional), "key": str<=80 (optional)}}

Stored card: `id, version, kind: ask, state: open|answered|resolved, title, text,
options, recommend, key, choice, answer, answered_by`. An answer that names an option (its
text, case-blind, or its 1-based number) is a `choice`; anything else is kept verbatim as
free text — never guessed. A malformed card gets ONE rewrite request in the same session
and attempt; a second bad card is ignored and the exit is judged as it stands.

Delivery: ntfy `lumen-fleet` (high) at park; re-sent by the sweep if the first send
failed; reminders every 24 h, at most 3. The card also rides `wq decide --prompt`, so
every existing Frank-gate surface (session briefing, `wq list --status pending_review`)
shows it with the answer command.

## Resume guarantees

- **Never swallowed**: the answer is peeked after the claim and consumed (`ANSWERED ->
  RESUMED`) only when every spawn gate has passed; a deferred dispatch leaves it intact.
- **Never twice**: consumption happens before the spawn.
- **Never session-dependent**: if `--resume` dies before an init frame (session pruned),
  fworker starts a fresh session in the same worktree, same attempt; `decision_context()`
  appends "DECISION ALREADY MADE …" to the prompt of ANY fresh session on a ticket whose
  last card was answered (fallback, crash after resume, later attempts).
- **Uncharged**: a parked leg writes no attempt record, so park + resume is one attempt
  and `make_worktree()` adopts the same worktree (wq#4765 precedent).
- Exempt from the wq#5007 24 h session expiry; the circuit breaker holds its counters
  steady on a parked loop.

## Stop hook / exit gate

A stop on a valid card is a park: `stop_gate.decide()` allows it before any convergence
nudge, and fworker handles the card before classification, so the wq#5007 exit gate never
sees a parked leg.

## auto-dispatch v3 (`/home/lumen/bin/auto-dispatch`, staged, still NOT enabled)

Runs `park.py sweep` at the top of every pass, before the halt files (it reconciles,
never dispatches). A ticket with an ANSWERED round is a resume, not a new dispatch: first
in order, exempt from the redispatch cooldown and the per-day cap, not counted by the
failure breaker, dispatched inside waking hours with a small queue. Halt files, broker
conflict, budget, concurrency and per-ticket gates still apply.
`park_resume.enabled: false` switches the hook off. Diff: `auto-dispatch/auto-dispatch.v3.diff`.

## Rollback

`FOREMAN_PARK=off` (fworker + Stop hook ignore cards; old behaviour). auto-dispatch:
`park_resume.enabled: false`, or restore `auto-dispatch.bak.20261116`.

## Side effect worth knowing

`wq decide` launches a detached judge-shadow job per call (wq#2799). That is existing
behaviour for every decision gate; tests set `FOREMAN_JUDGE_SHADOW=0`.

## Tests

`tests/test_wq5009_park.py` (card protocol, exhaustive transition table, real `wq` round
trip on a scratch DB: park, wait, answer, resume once, free text, second round, hop
limit, hand-moved ticket, rejected answer, undelivered card + reminder cap, cold read),
`tests/test_wq5009_park_integration.py` (real fworker path: park frees the slot with
nothing running, same session resumes with the answer in one attempt, dead-session
fallback, malformed card rewrite, hop limit, off switch) and 14 cases in
`auto-dispatch/test_auto_dispatch.py::TestParkResume`.
