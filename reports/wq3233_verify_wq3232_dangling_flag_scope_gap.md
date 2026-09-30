# wq#3233 verify: wq#3232 acceptance.py deterministic AC derivation can mint an unsatisfiable AC1

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author).

PR: #1497, merged to `main` as `a2c09e5c15fd846e24f8cbe4a4073191727e0943`. Files:
`harness/acceptance.py` (+7), `harness/tests/test_acceptance_derivation.py` (+13).
No other files touched.

## What wq#3232/PR#1497 claimed

wq#3231's `acceptance.json` (deterministic derivation) pinned an AC1 of `` `python -c` ``
(two tokens, no trailing code argument), `expect_exit: 0` — unsatisfiable by construction
since `acceptance.json` is pinned at dispatch and `python -c` always needs a following
code argument. Root cause: `_command_candidates()` trusts every backtick-quoted span in
ticket text unconditionally, and `replayable()` only checked shell-metacharacter-freedom
and `argv[0]` allowlist membership, never invocation completeness. The ticket's own FIX
text: "reject a candidate whose last token is a flag that requires a following argument
(-c/-e and similar) with nothing after it in the same backtick span... Add regression
coverage: a ticket detail containing a `python -c` wrapper phrase (this exact wq#3231
shape) must not produce a test-proof-type criterion for it."

Shipped fix: `_ARG_REQUIRING_FLAGS = {"-c", "-e", "-m"}` in `harness/acceptance.py:61`;
`replayable()` now rejects any command whose last shlex token is in that set
(`acceptance.py:91-92`). Regression test added:
`test_wq3232_backticked_incomplete_python_dash_c_is_not_a_command`, which reproduces the
exact wq#3231 ticket-text shape and asserts no criterion is derived.

## What I checked

- Read `harness/acceptance.py` cold: `replayable()`, `_command_candidates()`,
  `_looks_like_prose()`, `_derive_deterministic()`. Confirmed backtick spans bypass
  `_looks_like_prose` entirely (only the bare-line branch calls it) — matches the ticket's
  own root-cause claim.
- Ran the full suite: `python3 -m pytest -q harness/tests/test_acceptance_derivation.py`
  → **7 passed**, including the new regression test. Matches the claim.
- Reproduced the exact wq#3231 shape end to end via `_derive_deterministic()` on ticket
  text carrying `` `python -c` `` inside a sentence: `_command_candidates()` still finds
  the span, but `replayable('python -c')` now returns `(False, "'-c' requires a following
  argument, none present")`, so the deterministic derivation mints **no** test-type AC for
  it — only the fallback citation criterion survives. Confirmed for `python3 -c`, `node
  -e`, and `bash -c` too (all three families the fix's flag set covers).

## Contradiction found (CONFIRMED)

**The fix patches the three specific flags named as *examples* in the ticket (`-c`/`-e`/`-m`)
rather than the general class the ticket titled and root-caused: "a backtick-quoted span
whose last token requires a following argument."** Any other argument-requiring flag in a
backtick span still mints an unsatisfiable AC1, reproduced live against the shipped code,
not a rewritten copy:

```
$ python3 /tmp/fworker-wq3233/probe2.py
replayable('pytest -k'): (True, '')
derived criteria:
  {'id': 'AC1', 'text': '`pytest -k` passes', 'proof_type': 'test', 'cmd': 'pytest -k', 'expect_exit': 0}
  {'id': 'AC2', 'text': 'the change is real: cite file:line ...', 'proof_type': 'citation'}
```

ticket text used for that derivation (deliberately shaped like wq#3231's own — a backtick
span naming a technique, not literally invoking it):

```
if the failure is intermittent, narrow it down with `pytest -k` to select just the
flaky test by name, then bisect from there
```

`pytest -k` is a real dangling flag: pytest's own argparse requires a value for `-k` and
exits non-zero when it's missing. Confirmed against the actual binary, not inferred:

```
$ pytest -k
ERROR: usage: pytest [options] [file_or_dir] [file_or_dir] [...]
pytest: error: argument -k/--keyword: expected one argument
EXIT:4
```

`pytest` is in `REPLAY_ALLOWED_BINARIES` and is not `git`, so `_derive_deterministic()`
keeps this candidate and mints `AC1: `pytest -k` passes, expect_exit: 0` — exactly the
same "unsatisfiable AC1 from a backtick-quoted incomplete invocation" failure mode wq#3232
is titled to have fixed, just outside the three-flag allowlist the implementation chose.
This is the wq#2290 falsifiability trap by name: the one regression test added
(`test_wq3232_backticked_incomplete_python_dash_c_is_not_a_command`) is drawn entirely
from the population the fix was built around (`-c`/`-e`/`-m`) and cannot fail against the
general defect the ticket titled and root-caused — it confirms the patch, never tests its
boundary. `-k` is not a contrived pick either: pytest's own `-k`/`-x`/`--maxfail`,
Python's `-W`, and several other allowlisted-binary flags share the same "requires a
trailing argument, absent it is a usage error, not exit 0" shape and are all still
unguarded.

The alternative approach the ticket also offered ("extend `_looks_like_prose` to backtick
spans") would not have closed this either: `pytest -k` is 2 tokens with no
`_PROSE_TOKENS` function word among them, so `_looks_like_prose(["pytest", "-k"])` is
`False` — it reads exactly like a legitimate short invocation by that heuristic's own
rules. Closing the general class needs either a broader per-tool table of
argument-requiring flags, or a structural check (last token starts with `-`, is short,
and is not a known no-argument flag) — not an enumerated 3-item set.

## What I did not find broken

- The exact wq#3231 shape (`python -c`, `python3 -c`) and the two adjacent flags the fix
  chose to also cover (`node -e`, `bash -c`) are genuinely fixed — no test-type AC1 is
  minted, matching the shipped claim for that scope.
- No regression in existing behavior: all 6 pre-existing tests in
  `test_acceptance_derivation.py` still pass; a complete invocation (e.g. `` `pytest -q
  harness/tests/test_evidence_gate.py -k derivation -x` ``, the existing "long backticked
  command is trusted" test) is unaffected since its last token is never in
  `_ARG_REQUIRING_FLAGS`.
- Ordering/placement of the new check in `replayable()` (after empty-argv, before the
  binary-allowlist check) is correct and doesn't shadow or get shadowed by the existing
  shell-metacharacter check.

## Action taken

Contradiction posted to wq#3232 via `wq note`. Not re-implementing `replayable()`/
`_ARG_REQUIRING_FLAGS` per ticket instruction ("Do NOT re-implement; verify"). Escalating
per ticket policy to opus-5@high judgment. Fix shape for whoever picks it up: replace the
3-item `_ARG_REQUIRING_FLAGS` set with either (a) a structural heuristic — last shlex
token starts with a single `-`, is short (≤3 chars), and is not in a small known-zero-arg
set (`-x`, `-v`, `-q`, ...) — or (b) a per-allowlisted-binary table of flags that require a
following argument, and widen the regression test to draw from outside the `-c`/`-e`/`-m`
population (e.g. `pytest -k`) so it can actually falsify a narrow fix the way wq#2290's
post-mortem asked for.
