# wq#3047 verify-pass: wq#3033 [wq] free-text in the gate field makes a ticket unclaimable — CONTRADICT

**Verdict: CONTRADICT.** wq#3033's core fix is real and works exactly as claimed: `wq add`/`wq
edit` now refuse a `--gate` clause that is not an absolute or `~` path, closing the class where
acceptance prose (wq#2844, wq#2484) parses as a path that never exists and `claim --id` is
refused forever with no `--force` escape (wq#582). All 10 of the PR's own tests pass on `main`,
and a mutation check (running the same test file against the pre-fix binary) confirms the two
"refuses" assertions genuinely fail without the guard — the probe is falsifiable, not tautological.

But the PR's own source ticket (wq#3033) explicitly flagged a risk it needed to check and did
not: *"label-style gates on user-lane tickets (frank-bench-time, deploy_pending,
frank-after-reddit) may be intentional - check."* They are intentional. The guard as shipped
has no exception for them, and it now refuses the exact CLI invocation the fleet has documented
and used for over two months as the canonical way to populate them.

## Reproduction of the PR's own claims (fresh instance, worktree as shipped, no edits)

```
$ cd /home/lumen/bin && python3 -m pytest tests/test_wq3033_gate_path_guard.py -v
...10 passed in 7.31s

$ # mutation check: same test file against the pre-fix binary (parent commit 8477e8f)
$ WQ_BIN=/tmp/.../oldwq/wq python3 -m pytest tests/test_wq3033_gate_path_guard.py -v
test_add_refuses_prose_masquerading_as_a_gate FAILED   (old binary accepts the prose gate)
test_edit_refuses_prose_masquerading_as_a_gate FAILED  (same)
...other 8 PASSED identically
```

End-to-end repro of the bug class the PR fixes (old binary): `wq add --gate "done when
reviewed"` succeeds, then `wq claim --id <n>` is permanently `REFUSED: gate: done when reviewed
missing`, with wq#582's own claim-by-id path carrying deliberately no `--force`. On the new
binary the same `add` is refused up front instead — the fix is real.

## The break

`bad_gate_clauses()` (`wq:1085-1094`) flags any clause whose path segment doesn't start with `/`
or `~`. It has zero awareness of the gate column's other, older, documented use as a status
label — not a file-existence check — for `user`/build-staged tickets:

- `reports/staged_review_ui_design.md:30`: *"`wq done <id> ...` AND `wq edit <id> --gate
  "deploy_pending:<report_path>"`"* — this IS the designed protocol (wq#319, shipped
  2026-07-17) for a BUILD agent to mark its own staged work for Frank's approval UI.
- `reports/319_staged-review-ui.md:43-44`: *"Optional agent convention: `wq edit <id> --gate
  "deploy_pending:reports/..."` on staged closes to populate the Tracked tab."*
- Live `queue.db` (read-only query, real DB, not a fixture): 5 tickets carry a `deploy_*` gate —
  `319, 401, 402, 405, 407` (`gate LIKE 'deploy_%'`), spanning `done` and `blocked` status, the
  most recent as late as wq#407.
- Live `queue.db`: 9 currently-`open`, `user`/`frank-*`-lane tickets carry a bare label gate —
  `11, 160, 339, 1429, 1432, 1452, 1879, 1904, 1919` — all `frank-bench-time` or
  `frank-after-reddit` (`gate LIKE '%frank-bench-time%' OR gate LIKE '%frank-after-reddit%'`).
  These are exactly the two labels wq#3033's own ticket text named as "may be intentional."

Repro against the shipped `/home/lumen/bin/wq` (HEAD `52f576b`, main tip), scratch DB, real
convention string verbatim from `staged_review_ui_design.md:30`:

```
$ wq add --lane auto --pri 1 --title "staged review target"
added task 1 [auto]
$ wq edit 1 --gate "deploy_pending:reports/319_staged-review-ui.md"
REFUSED: --gate clause(s) not an absolute or ~ path: deploy_pending:reports/319_staged-review-ui.md (wq#3033)
wq gate is an enforced file-path precondition (comma list of path[:exists|missing]), not
acceptance prose -- a non-path clause can never resolve, so claim --id would be refused forever.
Put prose in --detail, a checkable probe in --liveness, or --force to override (discouraged).
```

Exit 1. The message is actively wrong for this input: this clause does not "never resolve" in
a harmful sense — it resolves vacuously. `gate_met()` (`wq:1071-1084`) splits on the last `:`
and only fails a clause when `cond` is literally `exists` or `missing`; `"reports/..."` is
neither, so both branches are false and the clause is silently treated as met. The `deploy_*`/
label convention was never actually at risk of the "claim refused forever" bug wq#3033 fixes —
it just shares the same column and the same absence-of-leading-`/` shape as genuine prose. The
new guard conflates the two populations wq#3033's own ticket said to keep apart.

## Why the shipped probe can't see this

All 10 tests in `test_wq3033_gate_path_guard.py` draw their "bad" inputs from unambiguous prose
sentences (`"netdata bundle confirmed live on both Mac Pros"`, `"some free-text acceptance
note"`, `"acceptance prose, not a path"`). None of them probe the boundary case the ticket text
itself called out by name (`frank-bench-time`, `deploy_pending`, `frank-after-reddit`). This is
the wq#2630 shape: a probe drawn only from the population the fix was obviously meant to catch,
never from the adjacent population the ticket explicitly asked the implementer to check before
shipping.

## Severity

Not a leak, not a crash, not a live-ticket regression today — every already-written `deploy_*`/
`frank-*` gate keeps behaving exactly as before (`gate_met()` is untouched by this PR). The
break is purely at the write path: any agent that follows the *documented* convention (`wq edit
<id> --gate "deploy_pending:<report>"` on a staged BUILD ticket, or a Frank-gate label on a new
`user`-lane ticket) is now refused, with a message that misattributes the refusal to the
prose-gate bug and offers `--force` as the only escape, discouraged in the same sentence. Left
as shipped, the next agent to close a staged BUILD ticket per `staged_review_ui_design.md`'s own
runbook will hit this refusal and either silently reach for `--force` (defeats the guard for
everyone, forever, on that ticket) or stop populating the Tracked tab.

## What I did not do

Did not re-implement `bad_gate_clauses()`. The fix is a judgment call on where to draw the
allow-list: hardcode the three named labels/prefixes (`deploy_pending`, `deploy_approved`,
`deploy_rejected`, `deploy_deferred`, `frank-bench-time`, `frank-after-reddit`, and whatever
else shares the pattern), or narrow the guard to only fire when a clause contains a colon AND
the tail after the last colon is not `exists`/`missing` (i.e. actually mirror `gate_met()`'s own
parse instead of independently re-deriving "looks like a path") — either choice needs someone to
decide the closed set of legitimate non-path gate values, which the original ticket deferred
("may be intentional - check") and this PR never resolved. Filing the escalation this
contradiction requires rather than patching here.

## Disposition

`wq#3047` closes as CONTRADICT. Filing `[judgment] wq#3047 verify-pass CONTRADICTED wq#3033:
gate path guard blocks the documented deploy_pending/frank-bench-time label-gate convention`
(opus-5@high per the archetype-8 escalation rule), deps on wq#3033, this report cited as
evidence. The `bad_gate_clauses()` guard and its two call sites (`wq:1526-1536`,
`wq:3414-3423`) stay on `main` — nothing here argues for reverting the prose-gate fix, which
correctly closes the class wq#3033 was filed for; only for adding the allow-list (or the
narrower colon-tail check) before the guard silently breaks the next staged-review or
Frank-gate ticket that uses the documented convention.
