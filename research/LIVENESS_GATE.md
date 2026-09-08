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
   frank_gated; SATISFIED -> `wq done --no-artifact "liveness: <detail>"` + note;
   STILL_BLOCKED -> surface; UNKNOWN -> skip silently, never close. Write bindings
   from the ledger into tickets as `Liveness:` lines.
4: ticket-generation lint refuses to file a frank-* ticket without a `Liveness:` line
   or with a premise the ledger already records as done.

## 11. Findings from the build (2026-09-07)

- wq#1133 / wq#1296 were reported CLOSED by the prior session; both are still
  `blocked`, `done_at` NULL. `wq done` refuses on an unclaimed ticket (wq:1546) and
  the orchestrator read `tail -1` as success. Ticketed wq#1479. Consequence for
  Slice 3: the close path is claim-then-done, never done alone, and MUST read rc.
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
