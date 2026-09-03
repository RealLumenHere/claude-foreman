# wq#1420 — verify-pass of wq#1415 (dispatch passes running every 2-4 min against the 20-min timer)

**Verdict: CONFIRMED.** PR#287 (merged, commit `bf92100` + merge `9672338`, now on
`origin/main`) does what it claims: it holds tickets stuck in a `HARNESS_DEFECT` loop
without touching the attempt-count runaway breaker, closing the gap that let
event-driven refill kicks re-dispatch the same preflight-refused ticket every 2-4 min
instead of at the 20-min timer cadence.

## What was claimed (wq#1415 / PR#287)

- Second caller of dispatch passes = `harness/fworker.py:2490` `trigger_refill()`
  (wq#992-B), kicked via detached `systemd-run` on every `EXIT_OK`/`EXIT_BLOCKED` worker
  exit — independent of and far tighter than the 20-min `lumen-foreman-dispatch.timer`.
- Real defect = `harness/fworker.py:2310` `handle_spawn_refused()` (a `SpawnRefused`
  preflight refusal, e.g. `CITED_PATH_UNREACHABLE`) never writes `attempt-N.json`
  ("attempt not charged") and releases the ticket straight to `open`, so
  `dispatcher/runaway.py`'s `stuck_ticket_ids()` (attempt-count based) never trips on a
  `HARNESS_DEFECT` loop — the 20-min timer used to bound the damage; the refill kick
  removed that bound.
- Fix: new `runaway.harness_defect_ticket_ids()` holds any open ticket whose current
  `failure.json` is still triaged `HARNESS_DEFECT`, independent of attempt count;
  `foreman_dispatcher.py` stage 3.5b (`handle_harness_defect_hold`) skips those tickets
  every pass with the same dedup-ntfy pattern as the existing `runaway_stuck` handler.
  Self-clears once a later attempt gets a fresh non-`HARNESS_DEFECT` `failure.json`, or
  the ticket leaves `open`.
- Tests: `dispatcher/tests/test_dispatcher.py -k "runaway or harness_defect"` (4 passed),
  full `dispatcher/tests/` (191 passed at the fix commit, 201 after rebase past wq#1408).

## Independent verification

- Read the fix commit (`bf92100`) diff directly: `dispatcher/runaway.py` adds
  `harness_defect_ticket_ids()` reusing the existing `candidate_ticket_ids()` cheap-first
  scan, reading `failure.json` per candidate, filtering on `triage == "HARNESS_DEFECT"`
  and `wq_show(tid).status == "open"` before holding. `foreman_dispatcher.py` adds
  `handle_harness_defect_hold()` (mirrors `handle_runaway_stuck()` exactly — same
  24h ntfy-dedup pattern) and wires it as stage 3.5b, computed once per pass
  (`harness_defects = runaway.harness_defect_ticket_ids()`), placed right after the
  existing stage 3.5 runaway check.
- Cross-checked the root-cause claims against `harness/fworker.py` at the branch tip:
  `handle_spawn_refused` (line 2310, comment "No attempt-N.json is written") and
  `trigger_refill` (line 2490, "kick one dispatch pass ... in seconds instead of at the
  next 20-min timer tick") match the PR body's citations exactly.
- Built two throwaway detached worktrees (`git worktree add --detach`, removed after) to
  run the real test suite rather than trust the PR's reported numbers:
  - At the fix commit `bf92100`: `python3 -m pytest dispatcher/tests/ -q` → **191 passed**
    (matches PR claim).
  - At the branch tip `9672338` (post-rebase over wq#1408's exception-boundary work):
    **201 passed** (matches PR's revised claim).
- Checked `evidence.json`'s AC1 citation (`dispatcher/foreman_dispatcher.py:915-923` at
  the tip) against the actual location of stage 3.5b in the tip file — the merge commit
  correctly re-pointed the citation after the rebase shifted line numbers (it was
  `820-828` at the pre-rebase fix commit, which would have been stale/wrong-code at the
  tip if left unresolved; it wasn't).
- Confirmed `bf92100` is an ancestor of current `origin/main` (`git merge-base
  --is-ancestor bf92100 origin/main`) — the fix is genuinely merged, not just sitting on
  an open branch.
- `wq show 1415` — `status: done`, notes cite PR#287 as merged with the same second
  caller / real defect / fix summary as the PR body.

## Adversarial checks that did NOT find a break

- Appended a scratch test (not committed, discarded) exercising three cases the PR's own
  tests don't cover directly: a corrupt (non-JSON) `failure.json`, a `failure.json`
  missing the `triage` key entirely, and a ticket with `triage: HARNESS_DEFECT` but
  `status: blocked` (not `open`). Ran it against the fix commit's worktree:
  - Corrupt JSON → caught by `except (OSError, ValueError): continue`, ticket dispatches
    normally, no crash.
  - Missing `triage` key → `rec.get("triage") != "HARNESS_DEFECT"` is `True` (`None !=
    "..."`), ticket dispatches normally.
  - `blocked` status → filtered out by `t.get("status") != "open": continue`, never held.
  - All three assertions passed (`1 passed` — see transcript; test not kept in the repo).
- Checked for a race between `harness_defects` being computed once per pass vs. a ticket
  transitioning mid-pass: the same one-scan-per-pass pattern is already used by the
  pre-existing `stuck_ticket_ids()` (comment: "one scan per pass, not per-candidate"), so
  this isn't new exposure introduced by this PR.
- Checked whether the fix could accidentally hold a ticket that got a fresh, real
  (non-harness) failure after a `HARNESS_DEFECT` one: `harness_defects` is recomputed
  fresh every pass from the *current* `failure.json`, so a new attempt overwriting it with
  a different triage clears the hold on the very next pass — no persistent "poisoned"
  state, matches the PR's self-clearing claim and is exercised by
  `test_harness_defect_clears_once_triage_changes`.
- `wq_show()` has no exception handling for `subprocess.TimeoutExpired`/`OSError` — a
  latent risk shared with the pre-existing `stuck_ticket_ids()` call site, not introduced
  by this diff. Out of scope for this verify pass; not re-flagged as a new finding.

## Conclusion

No contradiction found. The diff matches its own description, root-cause citations check
out against the actual `fworker.py` code, the acceptance-criterion file:line citation is
accurate post-rebase, the full test suite passes both at the isolated fix commit and at
the merged tip, and targeted adversarial inputs the author's own tests didn't cover
(corrupt/missing-key `failure.json`, non-open status) are handled safely. Board stays
CONFIRMED — no escalation to opus-5@high.
