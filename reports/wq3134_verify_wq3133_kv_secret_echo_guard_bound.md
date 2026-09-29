# wq#3134 — verify pass on wq#3133 (masking, PR#1411, f6cde81c)

**Verdict: CONFIRM.** The fix closes the exact defect wq#3132 reported; independently
re-verified with fresh boundary/adversarial probes, not just the author's own tests.

## What was claimed (wq#3133 / ISSUE-LOG row 1049/1050)
- wq#3132 found `_KV_NOT_ECHO` (`harness/masking.py`, wq#3127's kv-secret echo guard,
  commit 98e08381) spelled its prefix/tail chains with unbounded `+`/`*` repetition, so
  `self.<label>` followed by an ARBITRARILY LONG chain of `_`/`.`-joined lowercase words
  rode the guard past the alarm in full — no mask, no hit, no suppression record.
- Fix (`harness/masking.py:311-316`): introduces `_KV_ECHO_GAP = r"_{0,3}"` and
  `_KV_ECHO_PRE = GAP + (SEG[._]GAP){1,3}`, then rewrites `_KV_NOT_ECHO` as
  `(?:PRE KL (?:[._]SEG){0,2} | KL (?:[._]SEG){1,2})` — every repetition that was `+`/`*`
  is now a fixed `{m,n}` range (prefix 1-3 segments, tail 0-2/1-2 segments, underscore
  seam 0-3). Comment states the caps sit "just past" the longest real qualifier observed
  in the wild (`self.upstream_langfuse_<label>`, 3 segments).
- Also rewrote `reports/wq3132_judgment_..._unbounded_chain.md`'s literal probe line to
  use a `<label>` placeholder instead of the spelled-out shape, per ISSUE-LOG row 1050:
  once the guard is bounded, the pre-fix report's whole-value quote became a live
  credential-shaped string that armed the tripwire's own corpus scan.

## Independent verification (fresh probes, not just re-running the author's tests)
1. `python3 -m pytest harness/tests/test_masking.py -k "echo or lowercase_chain" -q` →
   14/14 pass, including the 4 new `test_a_lowercase_chain_too_long_to_be_an_identifier_is_a_credential`
   cases (4-segment qualifier chain, 40-underscore seam, extra tail words).
2. Full `test_masking.py` → 163/164 pass. The 1 failure
   (`test_the_mask_suite_does_not_arm_its_own_tripwire`, offender
   `harness/tests/test_read_dir_sweep.py: [('sk-key', 'sk-', 16)]`) is **pre-existing and
   unrelated**: reproduced identically after `git checkout 83adf407 -- .` (the commit
   immediately before this fix, before any wq3133 changes were applied), and the offending
   file was never touched by f6cde81c. Not a regression from this diff.
3. Adversarial probes run directly against `masking.mask_text()` (PYTHONPATH=harness),
   values built at runtime, never spelled whole in this file:
   - Genuinely unbounded-length inputs (50-segment dotted chain ~1KB, a bare 10,000-char
     run) now correctly return `hits=['kv-secret']` in ~2-9ms — no ReDoS, no silent
     swallow. This is the exact failure mode wq#3132 demonstrated pre-fix; it's closed.
   - Boundary sweep around the new caps: 3 qualifier segments / 2 tail segments /
     3-underscore seam all still suppress as `echo` (matches the documented intent); one
     unit past each cap (4 segments, 3 tail words, 5 underscores) flips to `kv-secret`
     with `suppressions() == []`. The `{m,n}` bounds are enforced exactly as written, not
     off-by-one in either direction.
   - Case-sensitivity and digit/opaque-tail guards (uppercase segment, digit segment,
     glued-not-seamed suffix) still correctly fall through to `kv-secret`, unchanged from
     wq#3127's original behavior.
   - One combinatorial curiosity, not a contradiction: because a label like `secret_key`
     is itself valid `SEG[._]SEG` grammar, repeating the label text can be reinterpreted
     by backtracking as generic prefix filler rather than "the label," letting a
     3-copies-of-the-label value (`secret_key=self.secret_key.secret_key.b`) suppress
     where a naive reading of "at most 3 prefix words then the label then at most 2 tail
     words" might not expect it. This stays inside the fixed, small budget the fix
     imposes (prefix ≤3 reps, tail ≤2 reps → a hard ceiling around 100-120 chars total,
     confirmed by probe) — it is slack within the new bound, not a reopening of the
     unbounded defect. A 4th copy of the label in the same shape correctly flips back to
     `kv-secret`. Noting it here for the record; not filing a follow-up.

## Conclusion
wq#3133's fix does what ISSUE-LOG row 1049 asked for: the previously-unbounded echo
chain is now bounded, arbitrarily long lowercase runs are caught again, and the
regression the fix itself caused in the corpus-tripwire tests (row 1050) was fixed in
the same commit. No new unbounded-suppression path found under adversarial probing.
The one failing test in the full suite is pre-existing, unrelated to this diff, and
reproduces identically on the pre-fix parent commit.
