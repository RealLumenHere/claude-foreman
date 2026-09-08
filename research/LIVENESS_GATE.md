# LIVENESS_GATE.md — per-ticket liveness probes as a `liveness:` sibling of `needs:`

Status: DESIGN + Slices 1-2 (build, report-only). Slices 3-4 (auto-clear / auto-close)
are OUT of this doc's build scope and gated on Frank's sign-off. Date 2026-09-07.

## 1. Root cause (evidence grade: A — reproduced live)

Frank-gated tickets nag on a premise that was true when the ticket was filed and is
never re-checked against live state. Reproduced this session against real tickets:

| ticket | premise on ticket | live state 2026-09-07 | verdict |
|---|---|---|---|
| wq#902 | privatize RealLumenHere/questfree | unauth GET -> 404 (not publicly visible) | STALE |
| wq#1296 | disable TS node-key expiry | every device keyExpiryDisabled | STALE |
| wq#1133 | rotate frankstupak PAT | /home/lumen/.secrets/github-frank.token -> 200 | STALE |
| wq#1047 | Cerebras 402 since 08-19 | see §6 (balance_over probe) | probe decides |
| wq#362 | hathor wedged/quarantined | tailscale Online=true | probe decides |
| wq#1101 | lumen-content + lumen-blog public | both private=false | STILL_BLOCKED |

The dispatcher's Frank-gate is a pure marker check — foreman_dispatcher.py:443
`frank_gate_reason()` reads title regex (:163 `FRANK_TITLE_RE`), `gate_type` without
`frank_response`, and `gate_met()` (:423); HUMAN_LANES (:132) adds the frank-* lanes.
None of it consults live state. Nothing does. So a stale premise survives every pass.

## 2. Class of defect

"Declarative per-ticket condition that is asserted at file time and never re-verified
at surface time." Where else it exists: the same shape was already fixed once for
capability — `needs:` tags are re-checked at every dispatch pass by
capability.preflight (capability.py:272) instead of trusted. The Frank-gate is the
one remaining condition on a ticket with no deterministic re-check. What makes the
class impossible: every frank-gated ticket carries a machine-checkable liveness
condition, evaluated by the same machinery that evaluates `needs:`, before the ticket
is surfaced to Frank.

## 3. Why it joins the broker chain and is not a silo

`needs:` is: declarative tag on the ticket (needs column + `Needs:` detail line,
needs.py:86 `parse()`), one parser (needs.py:21 `_TAG_RE`, :82 `parse_tags`), a
deterministic check (capability.py:236 `policy_grants`, :272 `preflight`), and a
routing decision (dispatcher skip reason / fworker SpawnRefused, fworker.py:837
`assert_spawn_capability`, :861 `resolve_exec_grant`).

`liveness:` is the same shape: declarative tag on the ticket (`Liveness:` detail
line), the SAME parser (`needs.parse_tags` via `needs.parse_liveness`), a
deterministic check (harness/liveness.py registry, surfaced through
capability.liveness_verdict), and a routing decision (Slice 3 — out of scope; Slice 2
only reports). Same file, same parser, same verdict vocabulary style.

## 4. Firewall — a liveness bug can never widen an exec grant

Enforced structurally, not by discipline:

1. `Liveness:` tags are parsed by `needs.parse_liveness()` into a separate
   `Liveness` object. They are NEVER appended to `Needs.tags`, so
   `policy_grants()` (capability.py:236), `needs_nodes()` (:221) and
   `resolve_exec_grant` (fworker.py:861) cannot see them. The needs column is
   validated by `needs.validate()` against `TAG_NAMES` (needs.py:19) — `liveness`
   and probe names are NOT added to that set, so `wq edit --needs liveness(...)`
   is rejected at write time.
2. `capability.preflight()` is byte-for-byte untouched. `liveness_verdict()` is a
   new, separate function that preflight does not call and that never returns a
   Verdict or a grant.
3. Probes are read-only: HTTP GET / systemctl is-active / tailscale status /
   os.path.exists. The single exception is `balance_over(cerebras, ...)` which sends a
   max_tokens=1 completion (that IS the surface that 402s; a models list returns 200
   regardless). No probe writes to wq, the grant file, or the policy.
4. fworker.py is not modified in Slices 1-2 (verified by git diff --stat).
5. Fail-safe: UNKNOWN is a first-class verdict and Slice 3 MUST treat it as
   "leave the ticket alone". Slices 1-2 do not change ticket state at all.

Rollback: `FOREMAN_LIVENESS_DISABLE=1` makes `parse_liveness()` return empty and the
sweep report every ticket as `no liveness tag`.

## 5. Tag syntax (Slice 2)

Detail line, same shape as `Needs:`:

    Liveness: repo_is(private, frankstupak/lumen-content), repo_is(private, frankstupak/lumen-blog)

Grammar is needs.py `_TAG_RE`: `name(args)`; args split on `,` and stripped. Multiple
probes on one ticket = ALL must be SATISFIED for the ticket to be STALE; any
STILL_BLOCKED -> STILL_BLOCKED; otherwise UNKNOWN (fail-safe). No DB column in this
slice — the `Needs:` detail line is already one of the two authored sources, so
`Liveness:` in the detail is the same shape with zero schema change. A `liveness`
column is optional later (wq1331_needs.py migration pattern).

## 6. Probe registry (Slice 1) — harness/liveness.py, deterministic, no LLM

| probe | args | SATISFIED | STILL_BLOCKED | UNKNOWN |
|---|---|---|---|---|
| cred_alive | path, verify_url[, scheme=bearer] | file present + GET verify_url 2xx | file missing, or 401/403 | timeout / other code |
| ts_key_expiry_disabled | — | every device keyExpiryDisabled | any device expiring | no API key / API error |
| repo_is | private\|public, owner/repo | auth 200 & flag matches; or (private) auth 404 + unauth 404 = not publicly visible | flag mismatch | API error / rate-limited |
| balance_over | provider, min | cerebras: 1-token completion 2xx; openrouter: /auth/key limit_remaining > min | cerebras 402; openrouter <= min | no key / other |
| path_exists | path | exists | missing | — |
| service_active | unit@node | `systemctl is-active` == active (ssh for non-lumen) | inactive/failed | ssh error / unknown node |
| node_healthy | name | tailscale Online=true AND `ssh <node> -- true` rc 0 | Online=false | not in tailscale status / ssh timeout |

Per-ticket aggregation and the sweep are in `liveness_sweep.py`.

## 7. Canonical ledger (Slice 2) — harness/liveness_ledger.yaml

One record per credential (name -> path, scope, verify_url, last_verified) and one per
done-capability (name -> since, evidence, probe). Plus `bindings:` (ticket id ->
Liveness tag string) as a staging area for tickets that predate the tag: the sweep
uses a binding when the ticket has no `Liveness:` line, so Slice 3 can write the
binding INTO the ticket once Frank signs off. Ticket-generation (wq add for frank-*
lanes) must read the ledger first: if a done-capability record covers the premise,
the ticket is not filed. Populated from what was verified 2026-09-07 (§1 + the
2026-09-02 CF token inventory in claude_credentials.md).

## 8. Sweep (Slice 2, read-only) — harness/liveness_sweep.py

Selects open/blocked/pending_review tickets the dispatcher would classify as
frank-gated (same predicate: HUMAN_LANES frank-* ∪ FRANK_TITLE_RE ∪ gate_type pending
∪ `[physical]`), evaluates each ticket's `Liveness:` line (or ledger binding), and
prints STALE / STILL_BLOCKED / UNKNOWN / NO_TAG. `--json` for machines. Exit 0 always.
Zero writes: no wq calls, no notes, no status changes.

## 9. Test-to-failure plan (executed in this session)

(i) sweep reproduces §1: 902/1296/1133 STALE, 1101 STILL_BLOCKED.
(ii) `Liveness:` parses via `needs.parse_tags` (unit test, same regex object).
(iii) `git diff --stat` on fworker.py = empty; `preflight` source unchanged (test
     asserts the function source hash / that liveness is not referenced in preflight).
(iv) UNKNOWN: a probe that raises or returns UNKNOWN leaves the aggregate UNKNOWN,
     never SATISFIED; sweep has no write path at all.
(v) firewall: `needs.validate("liveness(x)")` is rejected; `needs.parse()` on a detail
    containing a `Liveness:` line yields zero needs tags from it; `policy_grants` over
    a ticket with only a Liveness line returns ([], []).

## 10. Slices 3-4 (NOT BUILT — Frank sign-off)

3: dispatcher stage 4 calls `capability.liveness_verdict(ticket)` before skipping as
   frank_gated; SATISFIED -> close; STILL_BLOCKED -> surface; UNKNOWN -> skip
   silently, never close. Write bindings from the ledger into tickets as
   `Liveness:` lines.

   The close is `harness/wq_close.py`, never a bare `wq done` (wq#1479). The tickets
   Slice 3 closes are `blocked` and claimed by nobody, and that is precisely the shape
   `wq done` refuses: `wq claim --id` will not take a blocked ticket (`wq:1245`), so the
   close is unblock -> claim -> done, and a failure at any step must put the block back.
   `wq_close` owns that sequence, gates on every exit code, and confirms `status=done`
   against the row rather than against anything the CLI printed — which is the whole
   lesson of the 2026-09-07 false-closed report on wq#1133/wq#1296:

       python3 harness/wq_close.py <id> --notes "<verdict>" \
           --no-artifact "liveness: <detail>"     # exit 0 only when the row says done

   A SATISFIED verdict that ends `NOT-CLOSED` is a Slice 3 failure to surface, not a
   close to report.
4: ticket-generation lint refuses to file a frank-* ticket without a `Liveness:` line
   or with a premise the ledger already records as done.

## 11. Findings from the build (2026-09-07)

- wq#1133 / wq#1296 were reported CLOSED by the prior session; both are still
  `blocked`, `done_at` NULL, because the orchestrator read `tail -1` as success.
  Ticketed and fixed as wq#1479. Correction to this entry's first draft: `wq done`
  does NOT refuse on an unclaimed ticket — `owner_ok` returns True when `claimed_by`
  is empty (`wq:814`), and `wq:1546` is in `cmd_heartbeat`. Which guard refused here
  is unrecoverable; a refused close writes no `agent_activity` row. So the fix is
  guard-agnostic: close-path refusals now print to stderr, and `harness/wq_close.py`
  confirms `status=done` against the row instead of reading CLI output. Consequence
  for Slice 3 stands and is now enforceable: claim-then-done via `wq_close`, §10.
- Semantic trap caught on the first ticket-authored tag (wq#1478): a `Liveness:`
  probe must mean "the premise is DONE", not "the code that checks it exists" —
  the first draft would have auto-cleared a live decision. Slice 4's lint must
  reject a liveness probe that can be SATISFIED without the ticket's ask being met
  (rule of thumb: the probe's SATISFIED detail should read as the ticket's title in
  the past tense).
- Six tickets sit in status `frank_gated` (wq#662 shape) which the first sweep
  predicate missed; OPEN_STATUSES now includes it.
- `/home/lumen/.secrets/cf-email-routing.token` exists and verifies 200 while
  claude_credentials.md listed it as PENDING mint — doc drift, fixed same session
  and recorded in the ledger.

## 12. Slices 3-4 — auto-clear + creation gate (BUILT 2026-09-08, wq#1478)

Sign-off: Frank's 2026-09-07 build order ("a stale Frank-gate should auto-clear BEFORE it
reaches Frank"). The broker-grant PR (#342 -> 57491d3) merged before this landed, so the
file sets never overlapped: #342 touched capability_lint.py / fleet-exec-policy.yaml; this
touches liveness_clear.py (new), foreman_dispatcher.py (two hook lines), and /home/lumen/bin/wq
(cmd_add + cmd_decide, ~25 lines). fworker.py: untouched. capability.preflight /
policy_grants / effective_needs: untouched (pinned by hash in test_liveness_clear.py).

### 12.1 Where "Frank's queue" actually is (evidence)

There is no queue object. Frank sees a gated ticket through three surfaces, all computed
from queue.db at read time: (a) mission-control app.py:346-390 "awaiting Frank" (status
frank_gated/pending_review, or blocked with a Frank/gate/approval note); (b) the
dispatcher's `frank_gated` skip (stage 3 HUMAN_LANES, stage 4 frank_gate_reason); (c) the
orchestrator's end-of-session report. A ticket ENTERS the queue at exactly three writes:
`wq add` into a human lane / --frank-gated / title marker, `wq decide` (frank_gated ->
pending_review = "a queued question"), and `wq edit --status frank_gated`. So "before it
reaches Frank" = gate those writes (Slice 4 + decide hook) and keep the standing set
clean (Slice 3 sweep + dispatcher hook). Mission-control stays read-only (its QUEUE
INTEGRITY rule: writes go through the wq CLI), it just stops seeing closed tickets.

### 12.2 One write path: harness/liveness_clear.py

Every state change made by the liveness gate goes through `clear_if_stale(ticket)`:

    verdict = capability.liveness_verdict(ticket, bindings)      # read-only, Slice 2
    SATISFIED     -> audit line -> wq note -> normalize status -> wq_close.close() -> confirm
    STILL_BLOCKED -> nothing (Frank genuinely needed); audit line only on verdict change
    UNKNOWN       -> nothing to the ticket state; `wq note "[liveness] UNKNOWN ..."` ONCE per
                     verdict transition (state file dedupes), audit line, ntfy on first flag
    NO_TAG        -> nothing

Status normalization: `frank_gated`/`pending_review` are refused by `wq claim --id`
(wq:1180-1187), and `blocked` is refused too. wq_close.close() already handles blocked
(unblock -> claim -> done, re-block on failure). liveness_clear handles the two decision
statuses the same way: `wq edit --status open` first, and if any later step fails the
original status is restored by `wq edit --status <prev>` — a failed close must never leave
a gated ticket sitting `open` (that would widen the dispatchable queue).

Close is confirmed the wq#1479 way: the ticket row's own status after the close, never
CLI stdout. `wq note` and `wq done --notes` both carry the probe verdict verbatim:
`[liveness] auto-clear SATISFIED: repo_is(private, X) -> X is private`.

Audit: harness/runtime/liveness_audit.jsonl, one JSON line per decision that changed
anything or flagged anything: ts, ticket, prior status, verdict, every probe result,
action, close verdict (CLOSED/NOT-CLOSED + step), actor. Plus a `wq note` on the ticket
and an ntfy to lumen-fleet (Frank ro) per auto-close — that is the "route to
orchestrator": the next session's `handoff verify` shows the episode, the ticket shows
the note, the channel shows the line.

State: harness/runtime/liveness_state.json = {ticket: {verdict, ts, detail}} — last
verdict per ticket. Used for (1) UNKNOWN flag dedupe (flag on transition only), (2) the
dispatcher re-probe TTL (§12.4), (3) `--json` reporting.

Bindings -> tickets (§10): with `--write-bindings`, a ticket that has a ledger binding and
no `Liveness:` line gets the binding appended to its detail as a `Liveness:` line via
`wq edit --detail` (read-modify-write from `wq show --json`). Done once; after that the
ticket is self-describing and the ledger binding is redundant.

### 12.3 Periodic sweep (deliverable 2)

`python3 harness/liveness_clear.py --apply` over the same frank-gated predicate as the
Slice 2 sweep (imported from the dispatcher, not copied). systemd:
lumen-liveness-clear.timer, every 30 min (OnCalendar *:07/37), RandomizedDelaySec=90,
service Type=oneshot, User=lumen, WorkingDirectory=/home/lumen/foreman,
WQ_AGENT=liveness-clear (registered via `wq whoami --register liveness-clear` at install;
the unit re-registers if wq refuses). Runtime bound: probes have 15-20 s timeouts and only
tagged tickets cost anything (7 of 44 today) — measured 2026-09-08: full sweep 19 s.
Default with no flag is `--dry-run` (report, zero writes) so a bare invocation is safe.

### 12.4 Dispatcher hook (deliverable 1, surface (b))

foreman_dispatcher.py stage 3 (HUMAN_LANES skip) and stage 4 (frank_gate_reason skip) call
`liveness_clear.dispatch_hook(tid, ticket_or_None, fetch, log)` right before logging the
`frank_gated` skip. The hook is cheap by construction: it consults liveness_state.json
and the ledger bindings, and only re-probes a ticket that (a) is known to carry a
liveness condition (ledger binding, or a prior sweep saw a `Liveness:` line) and (b) has
no verdict newer than FOREMAN_LIVENESS_TTL_MIN (default 30). A brand-new tagged ticket is
picked up by the next sweep (<=30 min) and by the creation gate at file time. If the
hook closes the ticket it emits `liveness_autoclose` and the loop `continue`s without the
frank_gated skip; every other outcome falls through to the unchanged skip. The hook is
wrapped in the per-ticket try (wq#1408): a liveness exception costs that ticket's hook,
never the pass.

### 12.5 Creation gate (deliverable 3, Slice 4) — /home/lumen/bin/wq

`cmd_add` and `cmd_decide` call `_liveness_creation_check(title, detail, lane, frank_gated)`
when the ticket would be Frank's (lane in the dispatcher's HUMAN_LANES, --frank-gated,
or FRANK_TITLE_RE on the title). The check imports harness/liveness_clear.py lazily
(FOREMAN_HARNESS=/home/lumen/foreman/harness); an import failure is fail-OPEN with a
one-line warning (a missing harness must never block filing a ticket — same shape as
configured_lanes()). Verdicts:

  - `Liveness:` line present, probes SATISFIED  -> REFUSED (exit 1) "premise already met":
    the thing is done, file nothing. `--force` files it anyway with the verdict noted.
  - every probe on the line is recorded under `done_capabilities:` in the ledger -> REFUSED
    "ledger says done since <since>" (no probe run needed; the probe is still run and
    reported for the record).
  - STILL_BLOCKED / UNKNOWN -> filed normally, verdict printed.
  - no `Liveness:` line -> filed with a WARNING that the gate can never auto-clear.
    FOREMAN_LIVENESS_LINT_MODE=strict makes this a refusal; default is warn, because
    most of Frank's real queue is physical acts (buy X, boot Y) with no machine probe.
  - `wq decide` on a SATISFIED ticket: refused AND the ticket is closed through
    clear_if_stale in the same call — decide is the act of queueing a question, and a
    question whose answer is already on disk is exactly the redo loop.

Semantic-trap rule (§11) is documented, not linted: no deterministic check can tell
"probe means the ask is done" from "probe means the checker exists". The lint prints the
probe's would-be SATISFIED detail next to the title so the author sees the mismatch.

### 12.6 Safety properties (Frank's SAFETY line)

- Deterministic: probes only; no LLM anywhere in the path.
- Firewalled: liveness_clear imports capability.liveness_verdict and wq_close only.
  test_liveness_clear.py pins the source hashes of capability.preflight, policy_grants,
  effective_needs, effective_add_dirs and fworker.resolve_exec_grant,
  assert_spawn_capability — any drift fails the test with "not liveness's change".
  The dispatcher hook runs AFTER stage 3.7 has already decided the ticket is not
  dispatchable; it can only close a ticket, never route one to a worker.
- Fail-safe: UNKNOWN never closes (aggregate: any STILL_BLOCKED wins, then any UNKNOWN,
  SATISFIED only when every probe is SATISFIED); NO_TAG never closes; a close that does not
  land restores the prior status.
- Audited: every close = audit jsonl + wq note + ntfy, with the probe verdict verbatim.
- Reversible: FOREMAN_LIVENESS_CLEAR=0 makes every write path report-only (dispatcher
  hook no-op, sweep dry-run, creation check warn-only); `systemctl disable --now
  lumen-liveness-clear.timer`; wq patch is a .bak'd 25-line block behind one function;
  a wrongly closed ticket reopens with `wq edit --status <prev>` (the audit line records
  prev status).

### 12.7 Test-to-failure (harness/tests/test_liveness_clear.py, against a scratch queue.db
copy under WQ_DB with the REAL wq binary)

(i)   seeded ticket, lane frank-cred, `Liveness: path_exists(<tmpfile that exists>)`,
      status blocked -> --apply closes it; row status=done; audit line present; note present.
(ii)  seeded `path_exists(<missing>)` -> STILL_BLOCKED, status unchanged, no note.
(iii) seeded probe that raises (monkeypatched registry) -> UNKNOWN, status unchanged, ONE
      note on first sweep, none on the second (dedupe), audit line flagged.
(iv)  hash pins on the exec allow/deny functions; `import liveness_clear` never imports
      fworker; policy_grants over a Liveness-only ticket still ([], []).
(v)   replay this session's set (902/1101/1133/1296/1047/362/1478) with probe results
      recorded from the live 2026-09-08 sweep -> only 1101 remains STILL_BLOCKED.
(vi)  failed close (wq done forced to fail) on a frank_gated ticket -> status restored to
      frank_gated, audit line NOT-CLOSED.
(vii) creation gate: add with SATISFIED probe REFUSED; --force files; strict mode refuses
      tagless; decide on SATISFIED refuses and closes.
