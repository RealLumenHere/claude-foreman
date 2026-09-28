# wq#3054 judgment: wq#3047 CONTRADICTED wq#3033 — the gate guard must mirror `gate_met()`

**Decision: narrow `bad_gate_clauses()` to mirror `gate_met()`'s own parse. No allow-list.**
A gate clause is refused only when `gate_met()` would actually ENFORCE it as a file check and
it cannot be one. Everything `gate_met()` never tests — the documented `label:value` marker —
is accepted again, because a clause the reader never tests can never produce wq#3033's
"claim refused forever". Shipped in lumen-bin: `wq` `gate_clause()` / `bad_gate_clauses()` /
`print_bad_gate()`, plus `tests/test_wq3054_gate_label_markers.py`.

## The two options the ticket posed, and why the mirror wins

wq#3047's evidence (reports/wq3047_verify_wq3033_gate_path_guard_breaks_label_gates.md) stands:
`bad_gate_clauses()` as shipped flagged every clause not starting with `/` or `~`, which refuses
`wq edit <id> --gate "deploy_pending:<report>"` — the close step
`reports/staged_review_ui_design.md:30` and `reports/319_staged-review-ui.md:43-44` define, live
on wq#319/401/402/405/407.

- **Allow-list the known label prefixes** (`deploy_*`, `frank-*`) — rejected. It needs a closed
  set nobody owns, and the set is wrong the first time someone invents a sixth label; the guard
  would then refuse a legitimate marker for a reason the message can't explain.
- **Mirror `gate_met()`** — taken. `gate_met()` (`wq:1071-1084`) splits on the last `:` and acts
  only when the tail is `exists` or `missing`. Any other tail is a marker: both branches are
  false, the clause is met, the ticket stays claimable. So "can this clause strand a ticket?" is
  answerable from the reader's own parse, with no vocabulary of blessed labels at all.

The judgment is really about which function owns the definition of a gate. wq#3033 gave the
writer its own, independent definition ("looks like a path"), and two definitions of the same
string is what produced the break. There is now one: `gate_clause()`, called by both.

## The ticket's parenthetical is inverted

wq#3054's text (and the source report, lines 101-103) phrases option B as "only flag a clause
when the tail after the last ':' is neither empty/absent nor exists/missing." Read literally,
that flags `deploy_pending:reports/319_staged-review-ui.md` — the exact clause the ticket was
filed to unblock — and accepts bare prose. The implemented sense is the inverse and matches the
report's own analysis: flag a clause whose tail IS absent/`exists`/`missing` (it will be
enforced) and whose path is not absolute or `~`.

## What ships

```python
def gate_clause(clause):            # the one parse, shared by reader and guard
    head, cond = clause.rsplit(':', 1) if ':' in clause else (clause, 'exists')
    return head.strip(), cond.strip().lower()
```

`bad_gate_clauses()` refuses a clause when either:
1. **enforced** (`cond` is `exists`/`missing`) and `head` is not an absolute or `~` path — bare
   prose (wq#2844, wq#2484), and relative paths, which resolve against whatever cwd the caller
   happens to have; or
2. it would be a **marker** but carries whitespace (or has an empty head) — a real marker is one
   token, `deploy_pending:reports/…`, `frank-bench-time:hold`; a sentence with a colon in it
   (`"verify: netdata bundle live on both Mac Pros"`) is not.

Rule 2 is the hole a naive colon-only mirror would have opened, and the probe covers it.

### Bare `frank-bench-time` stays refused — deliberately

Written with no `:value`, `frank-bench-time` is not a marker: `gate_met()` supplies
`cond='exists'` and enforces a check for a file of that name in the caller's cwd, which never
exists. It is inert today only because all 9 live rows (11, 160, 339, 1429, 1432, 1452, 1879,
1904, 1919) sit in HUMAN_LANES (`dispatcher/foreman_dispatcher.py:149`), which the dispatcher
never dispatches and whose `user` lane `claim` refuses outright (`wq:1580`, `wq:1614`). On any
agent lane the same shape re-opens wq#3033's class exactly. Blessing bare labels would have
required lane-awareness inside a string-level function plus a second copy of `HUMAN_LANES` in
`wq` — drift for no safety. Instead the refusal teaches the one-word fix, naming
`frank-bench-time:hold` in its own text, and existing rows are untouched (the guard is
write-path only; `gate_met()`'s behaviour is unchanged for every stored gate).

## Evidence

- `tests/test_wq3054_gate_label_markers.py`: **13 passed**. With wq#3033's own file:
  `23 passed in 15.29s`, i.e. all 10 wq#3033 assertions still hold unmodified.
- Falsifiable (wq#2630): the same file against the pre-fix binary (main tip `52f576b`, exported
  to a scratch path) → **9 failed, 4 passed** — every accept-case fails without the fix.
- End-to-end, not just message text: `test_a_marker_gate_leaves_the_ticket_claimable` adds a
  ticket gated `deploy_pending:reports/319_staged-review-ui.md` and claims it (no `gate:`
  refusal), while its control `test_a_forced_bare_label_gate_does_strand_the_ticket` shows a
  `--force`d bare label still yields `REFUSED: gate: frank-bench-time missing`. That pair is the
  actual justification for the decision, run rather than argued.
- Probe input is pinned, not a live read: the convention string is a module constant quoted
  verbatim from `staged_review_ui_design.md:30`, so the test cannot drift with the design doc.
- Regression: full `tests/` (minus `test_rotate_provider_keys_swap.py`, a script-style file that
  `sys.exit`s at import on any run) → `12 failed, 487 passed`. The same 12 fail identically
  against the pre-fix binary — pre-existing (`test_wq_done_gate_terms.py`,
  `test_wq_evidence_durability.py`, `test_wq_hygiene_reconcile.py`), untouched by this change.
- Logged as row 1010 in `/home/lumen/kb/ISSUE-LOG.md`.

## Disposition

wq#3047's CONTRADICT is upheld in full; wq#3033's fix is kept, not reverted — the prose-gate
class it closed stays closed on both branches of the new guard. The staged-review-ui close step
works again as documented, and no future label needs a code change to be legal.
