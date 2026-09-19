# wq#2032 — verify-pass on wq#2027 / PR #650 ("intel->proposal source-grounding gate")

**Verdict: CONTRADICT.** Part 3 of PR #650 — `sweep_empty_source_proposals.py`,
the piece the PR's own `README.md` says "is what actually resolves
`ff80d5e94515`/`37cb4ce26a9b`" (README.md:51-56, quoting the eval doc's own
recommendation) — filters on `WHERE p.status = 'pending'`
(`sweep_empty_source_proposals.py:44-50`). No code path ever writes that
literal string into a real proposal row, so the sweep is a no-op against the
exact two fabricated proposals it was written to reject.

## What PR #650 claims

Three fail-closed layers, "all fail-closed" (README.md:24). Parts 1
(`get_candidates()` eligibility gate) and 2 (`source_grounding_check()` /
`verify()` wiring) are well-designed and independently verified below — see
"Not in scope" — and covered by 16/16 passing tests in
`test_source_grounding.py` (re-run here unchanged). Part 3, the sweep, is
presented with equal confidence but has **zero test coverage**: the README
itself scopes `test_source_grounding.py` to "regression tests for **parts 1
and 2**" (README.md:89) and the test file contains no reference to
`sweep_empty_source_proposals` at all (`grep -n sweep
test_source_grounding.py` — no hits).

## Break attempt

`save_proposal()` in `minerva_proposal.py` (the ONLY function that inserts a
row into `proposals`, pre-existing code the PR patches around but does not
touch) writes:

```python
proposal.get('decision', 'pending'),   # -> the `status` column
```

`decision` is a required field in `PROMPT_TMPL`'s output schema
(`"decision": "accept|reject|defer"`, minerva_proposal.py:231) — every
successful generation produces one of those three literal strings, never
`'pending'`. `'pending'` is only the SQL schema's `DEFAULT` value
(`ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py:30`,
the PR's own cited schema precedent), which `INSERT` overrides explicitly on
every row. Independent confirmation from the rest of the codebase: other
ops scripts read/write this same column as `'reject'`
(`ops/wq2019-proposal-reviewer-fix/reopen_stale_rejects.py:15,27`) and as
`'deploy_ready'|'planned'|'staged'|'rejected*'`
(`ops/wq1500-minerva-promoter/README.md:19`) — never `'pending'`.

`reports/2032_probe_sweep_status_never_pending.py` builds a scratch sqlite
db with the exact schema PR#650's own precedent test uses, seeds one row
shaped like the named defect proposal `37cb4ce26a9b` (source
`classified_intel` row with NULL url/summary, `status='accept'` — the LLM
decided to adopt the fabricated repo, which is the entire point of the
defect), and runs the real, unmodified `sweep_empty_source_proposals.py`
against it:

```
$ python3 reports/2032_probe_sweep_status_never_pending.py
BUG  status='accept' (what save_proposal() actually writes for an adopted-but-fabricated proposal) -> sweep reports: '0 pending proposal(s) sourced from a classified_intel row with no url/summary'
ok   status='pending' (literal, never actually written by save_proposal()) -> sweep reports: '1 pending proposal(s) sourced from a classified_intel row with no url/summary'

1/2 cases the sweep script fails to catch
```

The sweep only works on a status value that never occurs in production. On
data shaped exactly like the row it exists to catch, it reports zero matches
and silently no-ops.

## Why this matters for the verdict

wq#2027's defect is specifically that `ff80d5e94515` and `37cb4ce26a9b`
scored `verify()=1.00` and — per the eval doc's own recommendation
(`AGENTFORGE_EVAL.md:196`, "recommend REJECT both... wq#2027 step 3 sweeps
the class") — remain live, unrejected proposals today. Parts 1 and 2 stop
*new* fabricated proposals from being generated or passing verify from now
on; they do nothing for the two that already exist, which is exactly what
part 3 is for. Since `sweep_empty_source_proposals.py --apply` cannot match
either row (both almost certainly carry a real `decision` value, not the
literal string `'pending'`), running the documented deploy step (README.md
"Deploy" section: `python3 .../sweep_empty_source_proposals.py --apply`)
leaves `ff80d5e94515`/`37cb4ce26a9b` exactly as fabricated and exactly as
live as they were before this PR landed — the one concrete, named
consequence of the defect that this PR claims to close.

## Not in scope / not reproduced as new findings

- Parts 1 (`get_candidates()`) and 2 (`source_grounding_check()`/`verify()`
  wiring) read correctly and their 16/16 tests pass as claimed; no bug found
  in either. `verify()`'s new kwargs are opt-in and additive
  (`minerva_verify.py:860`, only activates when a caller passes
  `source_url`/`subject_claim_text`) — confirmed backward-compatible via the
  suite's own `wq2027_no_opt_in` case.
- `source_grounding_check()` degrades to a silent pass-through if
  `extract_claims(subject_claim_text)` returns zero claims (e.g. a very
  short title/proposed_change) — `unsupported_claims` is then vacuously
  `[]` and the check reports no defect. Not raised as a separate finding:
  the real call site (`minerva_proposal.py:557`) always passes
  `f"{title} {proposed_change}"`, which is never short enough in practice
  to hit this, and it is strictly narrower than the confirmed finding above.
- `fetch_source.py` and `quarantine_fabricated_notes.py` (both new, both
  admittedly out-of-reach of any sandbox per their own docstrings/README) —
  read, no bugs found, not independently exercised (network-dependent,
  `/home/lumen/minerva` unreachable from this worktree, same constraint the
  PR documents for itself).
- `install.sh`'s backup/apply/import-health/auto-revert shape — read, not
  executed (would touch `/home/lumen/minerva`, outside this worktree's
  reach).

## Reproduction

`reports/2032_probe_sweep_status_never_pending.py`, no DB/network touched
(scratch sqlite files created and deleted under `reports/`) — output pasted
verbatim in "Break attempt" above.

Not implementing a fix here — this is a verify-pass, not a re-implementation.
Fix shape for the escalated judgment: either (a) change
`save_proposal()`/the sweep's `SELECT` to agree on what "not yet
resolved" means for a proposal's `status` (e.g. sweep should match
`status IN ('accept','defer')` — a proposal a human/eval pipeline hasn't
already independently rejected — rather than the never-used `'pending'`),
or (b) if `'pending'` really is meant to be a distinct lifecycle state
written by some other stage this session couldn't reach, name that stage
and confirm it runs before the sweep in production. Either way, add a test
for `sweep_empty_source_proposals.py` seeded with a `status` value
`save_proposal()` actually produces, and re-run
`python3 sweep_empty_source_proposals.py --apply` against the live
`ff80d5e94515`/`37cb4ce26a9b` rows to confirm they are actually rejected
before this is trusted.
