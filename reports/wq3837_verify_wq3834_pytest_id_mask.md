# wq#3837 — verify-pass of wq#3834 [rotation] burned credential sha256:100199d36e44 (kv-secret)

**Verdict: CONFIRMED.** wq#3834's fix is correct and the regression it closes is real — both
masked under the pre-fix code and clean under the post-fix code, reproduced independently
rather than trusted from the diff's own narration. One self-inflicted finding surfaced during
this verify-pass (wq3834's own `kb/ISSUE-LOG.md` closure row re-armed the exact tripwire its
fix exists to satisfy) — fixed in place, documented below, not treated as a contradiction of
the code fix.

## What wq#3834 claimed

- pytest spells a `str` parametrize value's auto id as the value itself, so `-v` /
  `--collect-only` print any credential-shaped fixture whole — this is how wq#3830 attempt 2
  paged `kv-secret sha256:100199d36e44` (prefix `credentials=`, len 35) off a listing, not off
  a leaked real secret.
- Fix: `harness/tests/id_mask.py` adds a `pytest_make_parametrize_id` hook that masks any
  credential-shaped `str` id via `masking.mask_text`; wired into both load paths — the
  repo-root `conftest.py` (loaded by file, mirroring the existing `live_bin_guard` pattern) and
  `harness/tests/conftest.py` (`from id_mask import pytest_make_parametrize_id`), so a run from
  either directory gets the hook.
- A second, adjacent leak on the same listing: `_WQ2848_NOISE_CASES`' default `-`-joined
  parametrize id reads cloudflare's `prefix+floor+noise` as one `cfut_`-prefixed token (because
  cloudflare's body charset `[A-Za-z0-9_.-]` admits dashes) — switched to an explicit
  `+`-joined id.
- New test `test_this_files_case_ids_read_silently` runs a real `--collect-only -v` subprocess
  from `harness/tests` (where the root conftest never loads) and asserts the output doesn't
  re-arm `masking.mask_text`.

## What I checked, independently of the diff's own narration

1. **Ran the new/changed tests.** `test_this_files_case_ids_read_silently`,
   `test_a_bare_foreign_prefix_substring_does_not_drop_the_whole_token`, and
   `test_a_lowercase_chain_too_long_to_be_an_identifier_is_a_credential` all pass (15 cases,
   `harness/tests/test_masking.py`).
2. **Watched the hook fire live**, not just via the test's own assertion:
   `pytest --collect-only -q harness/tests/test_masking.py -k
   test_a_lowercase_chain_too_long_to_be_an_identifier_is_a_credential` from both
   `harness/tests` and the repo root prints `...[credentials=MASKED]` / `...[secret_key=MASKED0]`
   — confirmed both conftest load paths actually mask, not just that the probe test passes.
3. **Reproduced the pre-fix bug directly**, rather than taking the diff's "measured" claim on
   faith. Loaded `masking.mask_text` and fed it the id pytest would have generated *before*
   this fix (verified empirically by temporarily swapping in the parent commit's
   `test_masking.py`, running `--collect-only`, then restoring via `git checkout --`):
   `test_..._bare_foreign_prefix...[2-cloudflare-cfut_<value>]` (pytest's default `-`-join) →
   `mask_text` returns `(..., ['cloudflare'])` — a real hit, because cloudflare's floor-6 body
   charset includes `-`, so `-6-AKIA` (7 chars) clears the floor right after `cfut_`. The fixed
   `+`-joined form, `[2-cloudflare+cfut_+6+AKIA]`, returns `(..., [])` — no hit, since `+` isn't
   in cloudflare's charset and breaks the match. This is the exact bug class the ticket
   describes, confirmed to be real and confirmed fixed, independent of the PR's own test.
4. **Checked the double-load trick isn't buggy.** `id_mask.py:_masking()` loads both
   `live_code` and `masking` by file into `sys.modules` under their plain names, but only
   returns `masking`. This isn't dead weight — `harness/masking.py` does a normal top-level
   `import live_code`, which resolves through `sys.modules` lookup rather than a file load;
   pre-seeding `sys.modules["live_code"]` is what makes that import succeed when `masking.py`
   is loaded out-of-tree via `spec_from_file_location`. Confirmed by reading
   `harness/masking.py:30` and `harness/masking.py:683/718`.
5. **Ran the full masking suite**, not just the touched tests: `pytest harness/tests/test_masking.py`
   — 189 passed, 1 failed (`test_doctrine_corpus_does_not_re_arm_the_alarm`, see below; unrelated
   to the code diff, traced to live external state).
6. **Checked collection-wide blast radius** of the `conftest.py` changes: `pytest
   harness/tests/ --collect-only -q` — 3714 tests collected, no collection errors. The new
   root-conftest hook doesn't break anything else that loads through it.

## The one failure found, and why it isn't a contradiction of the code fix

`test_doctrine_corpus_does_not_re_arm_the_alarm` (pre-existing test, unrelated to this diff —
`git blame` dates it to `64b4e5036`, 2026-09-20) scans the live, shared `/home/lumen/kb/ISSUE-LOG.md`
for anything that re-arms `masking.scan`. It failed after wq#3834 landed, with two hits:

- **Row 1275** (wq#3778, pre-existing, predates wq#3834 entirely): its own prose quotes the
  *pre-fix* literal `'[selfheal] rotate key: ...'` text for illustration. Verified in isolation
  (`masking.scan` on just that row) — it fires on its own, with or without wq#3834's commit.
  **Not caused by wq#3834; out of scope for this ticket** (belongs to wq#3778's lineage).
- **Row 1294** (wq#3834's own `issue_log_append.py` closure row): its prose reads `"not a
  credential:
  test_masking.py's lowercase-chain kv-secret fixture..."` — the generic kv-secret
  chain rule matches `credential: ` glued directly to a lowercase dotted identifier regardless
  of the preceding negation. wq#3834's own doctrine-mandated audit entry re-armed the exact
  tripwire its fix exists to defuse. Verified in isolation the same way.

This is a process/log-hygiene defect in wq#3834's ticket-closure bookkeeping, not a defect in
the shipped code (`id_mask.py`, the conftest wiring, and the `+`-join are all independently
verified correct above, including against adversarial inputs the PR's own tests don't cover).
**Fixed during this verify-pass**: reworded row 1294 to break the `credential:` + identifier
seam without changing its meaning (`Edit`, a back-reference-style change to an existing row,
per doctrine). Logged the finding as ISSUE-LOG row 1295 via `issue_log_append.py` — and then
had to fix row 1295 itself too, because my first draft quoted the offending phrase verbatim to
describe it, which re-armed the same rule a third time. Reworded to describe the shape
("label-colon-identifier") instead of quoting it. Re-ran the test after each fix:
only row 1275 (wq#3778, pre-existing, out of scope) remains failing.

## What would have broken this verdict (and didn't)

- If the fixed `+`-joined id had *also* cleared cloudflare's floor → would mean the fix doesn't
  actually close the leak, just changes its shape. Confirmed it doesn't: `+` isn't in
  cloudflare's body charset, so the match breaks at the first `+`.
- If `_masking()`'s unused-looking `live_code` pre-load were genuinely dead code → would mean
  the out-of-tree `masking` import might fail under different sys.modules states. Confirmed
  it's load-bearing: `masking.py` imports `live_code` at module scope, resolved via
  `sys.modules`.
- If the root-conftest hook broke collection anywhere else in the repo → would mean the fix's
  blast radius exceeds its stated scope. 3714/3714 tests still collect.
- If `test_this_files_case_ids_read_silently`'s subprocess run had been faked or mocked rather
  than a real `--collect-only -v` invocation → would mean the probe can't fail. It's a real
  `subprocess.run` against the actual file; confirmed it asserts on real stdout.

No escalation to judgment needed — board this as verified.
