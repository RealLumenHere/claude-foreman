# wq#2841: verify-pass of wq#2822 (mask tripwire corpus walk fails on main: judge/build_eval_)

**Verdict: CONFIRM.** wq#2822 did exactly what it claimed for the three files named in
its ticket, verified independently against its own merge commit, not just its own
notes. Full corpus scan and the full masking suite both go green at that commit.

## What wq#2822 shipped (PR#1164, merged `56a720da03` / on-`main` as `53d4efa`)

Three one-line edits, all defusing a whole `"sk-" + "lumen-litellm-master"`
(concatenated) credential shape (the confirmed-dead May-2026 LiteLLM master-key
placeholder) that had landed as a literal string in three files, arming
`test_the_mask_suite_does_not_arm_its_own_tripwire` (wq#2749's whole-tree
tripwire-corpus walk, `54129aa`/`7456b22`) on mere read:

- `judge/build_eval_growth.py:440` — the `redteam-10` case's embedded curl body:
  `"sk-" + "lumen-litellm-master"` (concatenated) → `"key-lumen-litellm-master"`.
- `judge/judge_eval_set.jsonl` — same replacement in the already-generated eval row.
- `reports/2756_rotation_triage.md:19` — prose citation split at the prefix boundary:
  `` `"sk-" + "lumen-litellm-master"` `` (concatenated, was previously one whole
  literal) → `` `"sk-"` + `"lumen-litellm-master"` `` (concatenated).

## Adversarial checks run

1. **Re-ran the claimed result at the shipped commit, not from notes.** Built a
   detached worktree at `53d4efa` (the commit actually on `main`'s first-parent
   chain — `56a720da03` from the PR notes is the identical patch on the PR branch tip,
   confirmed via `git diff 56a720d 53d4efa` = empty) and ran both probes independently:
   - `python3 -m pytest harness/tests/test_masking.py -q` → **102 passed**, matching
     the ticket's own done-notes exactly.
   - `python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman 53d4efa` →
     **offenders: 0**, matching the fix commit's own "Testing" section.
2. **Checked for generator/output drift.** `judge/build_eval_growth.py:main()` reads
   the existing `judge_eval_set.jsonl`, computes `existing_ids`, and only *appends*
   `CASES` entries whose `id` is not already present (`dup` raises `SystemExit`
   otherwise) — `redteam-10` is already in the existing file, so re-running the
   generator would never rewrite it. Editing both files independently was therefore
   required (the generator edit is not functionally inert, it's just non-propagating),
   and the two edits are semantically identical strings — no drift risk.
3. **Checked the eval semantics weren't weakened.** `redteam-10` tests whether the
   judge flags a mutating `POST /key/update` despite "this is read-only" framing —
   the assertion is on HTTP verb/endpoint, not on the key's string shape. Grepped for
   any test that keys behavior off the literal `"sk-"` prefix for this case
   (`grep -rn redteam-10 judge/*.py`) — none found; `judge_bench.py`/`judge_bench_drift.py`
   consume the eval set generically by `expected_verdict`/`gate_verdict`, unaffected.
4. **Checked the report edit preserved the value it documents.** The whole point of
   `reports/2756_rotation_triage.md:19` is recording a hash match; verified the split
   still reproduces the exact string and hash it cites:
   `python3 -c "import hashlib; v='sk-'+'lumen-litellm-master'; print(hashlib.sha256(v.encode()).hexdigest()[:12])"`
   → `1bd4635b5896`, matching the row's own `1bd4635b5896` cell — the edit didn't
   silently change what's being claimed.

No break found in wq#2822's own diff. Confirming.

## Scope note (not part of this verdict — same pattern as `reports/2787`'s own note)

Current `main` (`HEAD` at verify time, `77d3e8b`) is **red again** on the same test,
for a reason wq#2822 could not have anticipated: a *later* commit introduced a new
offender.

```
$ python3 -m pytest harness/tests/test_masking.py -k tripwire -q
FAILED — offenders: {'reports/2787_verify_wq2782_mask_tripwire_cloudflare_hold.md':
  [('kv-secret', 'CF_API_TOKEN=', 36)]}

$ python3 ops/wq2739-tripwire-corpus/scan_tree.py /home/lumen/foreman HEAD
offenders: 1
  reports/2787_verify_wq2782_mask_tripwire_cloudflare_hold.md -> [('kv-secret', 'CF_API_TOKEN=', 36)]
```

`reports/2787_verify_wq2782_mask_tripwire_cloudflare_hold.md` (landed as `b291f9a`,
*after* wq#2822's `53d4efa`) reproduces its own finding with a live, unsplit
`CF_API_TOKEN=<40 hex>` literal in a `python3 -c` repro block — the exact whole-shape
mistake wq#2749/wq#2822 exist to catch, this time self-inflicted by a verify-pass
report about a *different* mask ticket. Reading that file now arms the pri-1 rotation
tripwire. Filed as ISSUE-LOG row 875 (kb); not filing a new wq ticket per the wq#2787
precedent (its own report left an analogous later-commit break as a scope note only,
no separate ticket) — the periodic security scan that produced wq#2822 in the first
place (`sec-2-9d0da3`, per wq#2822's ticket detail) is the established path that turns
this class of corpus-walk offender into a dispatched fix ticket.

## Disposition

- `kb/ISSUE-LOG.md` row 875.
- wq#2822: CONFIRM. Board updated, no escalation (author's diff is correct; the new
  failure is unrelated and post-dates it).
