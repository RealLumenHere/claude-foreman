# wq#906 — Adversarial verify-pass of wq#662 (Frank-decision gate protocol, PR#34)

**Verdict: CONFIRMED — no contradiction.**

## What was shipped (PR#34, commit 39784ca in lumen-bin, merged 2026-08-23)

Additive columns (`gate_type`, `decision_prompt`, `orch_recommendation`, `frank_response`,
`response_ts`, `review_verdict`, `review_reason`), two new states (`frank_gated`,
`pending_review`), and three new verbs (`wq decide`, `wq answer`, `wq review`) in
`/home/lumen/bin/wq`, plus `probe_wq662.py` (47 assertions, transitions A-O).

## Verification evidence

### Probe: 47/47 GREEN, reproduced independently
Ran `probe_wq662.py` cold against the live `/home/lumen/bin/wq` binary (fresh scratch
`WQ_DB`, not the author's run) — `PASS 47 FAIL 0`, matches the ticket's closing note.

### Column-index shift in `cmd_list` — correct
Adding `frank_response` to the `list` SELECT shifted `created_at` from index 10 to 11.
Both `effective_pri(...)` call sites (initial `--sort effective` and the per-row `ep`
calc) were updated to `r[11]`; `r[9]` (gate) is untouched. Confirmed by reading
`effective_pri`'s signature (`pri, status, gate, created_at`) against both call sites —
no off-by-one.

### Claim guard — doubly enforced, not just string-matched
- `claim --id` on a `frank_gated` ticket: refused, `FRANK_GATED` in output (probe A.1,
  reproduced).
- `claim --id` on a `pending_review` ticket (not covered by the probe): refused,
  `PENDING_REVIEW — awaiting ORCH review` — reproduced manually.
- Lane-scan claim (no `--id`) never sees either state independent of the above: `cmd_claim`'s
  scan query is `WHERE lane=? AND status='open'` (wq:802), and neither new state is `'open'`.

### Argparse edge case — answer text starting with `-`
Tried `wq answer <id> "-1 hour delay, acknowledged"` and `wq answer <id> "--wait until
Monday..."` — both stored verbatim, no argparse misparse. Not a bug.

### Dispatcher integration — the one place `gate_type` is actually read
`foreman/dispatcher/foreman_dispatcher.py:frank_gate_reason()` fail-closes on
`gate_type set AND frank_response empty`. Traced the full lifecycle against this:
- `add --frank-gated`: gate_type set, frank_response NULL → correctly skipped.
- `decide` → `pending_review`: unchanged from above → still skipped (dispatcher only
  scans `--status open` anyway, so pending/gated tickets never reach the scan).
- `review reject`: frank_response cleared, gate_type untouched → still skipped, correct.
- `review accept` → `open`: frank_response **preserved** (probe G.4), status flips to
  `open` → dispatcher's compound condition is false (frank_response is non-empty) →
  ticket dispatches normally. No regression.

## One observation (non-blocking)

`cmd_review`'s own docstring claims verdict=accept results in "`status='open', gate_type
cleared`" — the code only sets `status='open', review_verdict='accept', review_reason=?`.
`gate_type` is never cleared on accept; reproduced live (`gate_type` stays
`'frank_decision'` forever on an accepted ticket). This is a real doc/code mismatch, but
not a functional regression today: the sole consumer of `gate_type` (the dispatcher's
`frank_gate_reason`) only fail-closes when `gate_type` is set **and** `frank_response` is
empty, and every reachable transition keeps those two in the safe combination (accept
preserves `frank_response`; reject clears `frank_response` but the ticket also returns to
`frank_gated`, where the skip is intentional). Flagging as tech debt: if a future change
ever clears `frank_response` on an accepted ticket without also clearing `gate_type`,
that ticket becomes permanently unclaimable with no override flag (by design — "fail
closed... there is no override flag"). Cheap fix: add `gate_type=NULL` to the accept
branch's UPDATE to match the docstring. Not filed as a separate ticket — one-line fix,
better left for whoever next touches `cmd_review`.

## Items NOT contradicted
- Migration is additive and idempotent (`if "col" not in cols` guard, same pattern as
  the existing `reopened_count`/`reopened_at` columns).
- `wq decide`/`wq answer`/`wq review` all use `BEGIN IMMEDIATE` — no race between
  concurrent callers re-reading a status a competing transaction already changed.
- The "orch-only" / "Frank/MC-only" role split from the ticket's acceptance criteria is
  not mechanically enforced — but the code says so in its own docstring
  ("enforced at the cultural level, not mechanically"), and no other wq verb in this
  codebase enforces caller *role* (only claim *ownership*, via `owner_ok`) — there is no
  role/identity infrastructure to enforce against. Consistent with existing design, not a
  gap introduced by this diff.
- `wq list` column widths (`status` 8→14, title 64→58) correctly fit `pending_review`
  (14 chars) without truncation.

## Disposition
Ship-quality. Probe is comprehensive and reproduces green independently. The one
docstring/implementation mismatch found does not manifest as a live bug given the
current sole consumer and the shipped transition set — recorded above for whoever next
touches `cmd_review`, not blocking.
