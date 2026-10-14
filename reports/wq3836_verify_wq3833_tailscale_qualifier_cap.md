# wq#3836 — verify wq#3833 (rotation: burned credential sha256:35c81c6c1c8b, tailscale)

**Verdict: CONFIRM**

Archetype 8 adversarial verify-pass of the diff shipped by wq#3833 (PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1826, merged to `main` at
`b510a345` — identical tree to the pre-merge branch commit `787a223b`, confirmed
by matching `harness/masking.py` blob hash `9cf717e6`).

## What wq#3833 claims

`_PRE_TAILSCALE`'s optional qualifier group was capped at 20 lowercase letters
(`harness/masking.py:92-101`, old `tskey-(?:[a-z]{1,20}-)?`). The tailscale body
floor is 16 (`_floored_body(r"[A-Za-z0-9-]", 16)`, `masking.py:569`). A re-read of
our own output `tskey-<15..20 letters>-MASKED` could take the kept qualifier as a
body on the second pass, since qualifier+dash (16-21 chars) reaches the floor —
`mask(mask(x)) != mask(x)`. This is exactly what happened to wq#3830's probe
output and filed a pri-1 rotation for a dummy value (ISSUE-LOG.md row 1293,
sha256:35c81c6c1c8b). The fix caps the qualifier at 14 letters, so qualifier+dash
is at most 15 — strictly under the 16-char floor — and adds
`test_a_long_tailscale_qualifier_is_still_a_fixed_point` parametrized at
qualifier lengths 14 (kept), 15 and 20 (both now masked as body).

## Verification performed

1. **Diff is what it claims to be**: `git show b510a345` — two files,
   `harness/masking.py` (regex cap 20→14, comment) and
   `harness/tests/test_masking.py` (new parametrized test). No other files
   touched; kv-secret/other credential classes untouched.
2. **Ran the shipped test**: `test_masking.py -k tailscale` → 3 passed.
3. **Ran the full masking suite**: 189 passed, 1 failed —
   `test_doctrine_corpus_does_not_re_arm_the_alarm`. Confirmed **pre-existing
   and unrelated**: the failure is a `kv-secret` match inside
   `/home/lumen/kb/ISSUE-LOG.md` row 1275 (wq#3778), which quotes a historical
   credential-shaped literal (`rotate key: api.deepseek.com`) in its own prose
   as documentation of a past incident. This is a live, externally-mutated
   corpus (other fleet workers append rows concurrently) scanned by a
   `kv-secret` rule the wq#3833 diff never touches — the diff's only code
   change is `_PRE_TAILSCALE`. Not in scope for this verify pass.
4. **Adversarial fuzz beyond the committed boundary cases**: for qualifier
   lengths 1-25 and body lengths {8, 15, 16, 17, 30}, ran
   `mask_text` twice and checked (a) idempotence (`mask(mask(x)) == mask(x)`,
   second pass fires no labels) and (b) no floor-length body survives in the
   clear. All 100 cases at body length ≥ 16 (the floor) hold — fixed point,
   no leak, for every qualifier length, not just 14/15/20. The 25 cases at
   body length 8 don't match at all (body under floor), which is the same
   documented accepted gap every other floored-body rule in this file has —
   not something wq#3833 was scoped to fix, and not a regression.
5. **Nested-qualifier adversarial probe**: `tskey-ab-cd-` + 16×`B` (a second
   lowercase-dash chunk ahead of the real body) still reaches a fixed point:
   masks to `tskey-ab-MASKED`, rescans clean. No new ambiguity introduced by
   the narrower cap.
6. **Traced the backtracking by hand** for the specific wq#3830 shape at the
   new cap (`tskey-` + 14×`q` + `-` + `MASKED`): on re-scan the optional group
   can only match at exactly the qualifier/dash boundary (any shorter match
   lands mid-run of `q`s, not on a dash) and that position's body is blocked by
   the `(?!MASKED)` guard; if the group doesn't match, the body alternative
   caps at 15 chars (`14×q + dash`), one under the 16 floor. Either way the
   second pass can't match — confirmed empirically in (4).

## Conclusion

The stated defect (qualifier cap ≥ body floor → self-re-arming on
`tskey-<qualifier>-MASKED`) is real, the fix (cap 14, strictly under floor 16)
closes it, the new test pins the exact regression shape, and broader fuzzing
found no case where the fix's claim doesn't hold. The one failing test in the
full suite run is pre-existing, caused by unrelated live KB content, and not
touched by this diff.

**CONFIRM** — no escalation.
