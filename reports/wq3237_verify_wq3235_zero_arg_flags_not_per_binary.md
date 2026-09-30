# wq#3237 verify: wq#3235 dangling-flag general fix accepts a global `_ZERO_ARG_FLAGS`
allowlist that isn't per-binary — `git checkout -b` / `git branch -u` still mint an
unsatisfiable pinned AC1

Archetype 8 adversarial verify pass. Author: claude-opus-5. Verifier: claude-sonnet-5
(fresh instance).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1500, merged to `main` as
`d7003d3d809fbce9b1c5ac8d9455eed1a9f93a37`. Files: `harness/acceptance.py` (+33/-9),
`harness/tests/test_acceptance_derivation.py` (+53). No other files touched.

## What wq#3235/PR#1500 claimed

wq#3233 contradicted wq#3232: the shipped `_ARG_REQUIRING_FLAGS = {-c, -e, -m}` allowlist
only covered the three example flags named in wq#3231's ticket text, not the general
"backtick span whose last token requires a following argument" class wq#3232 was titled
and root-caused for — `pytest -k` still minted an unsatisfiable `AC1: pytest -k passes,
expect_exit 0` (pytest actually exits 4). wq#3235's fix inverts the default: `replayable()`
now rejects any pre-pin command whose last shlex token looks like a flag (`_FLAG_TOKEN_RE`)
and isn't in a new `_ZERO_ARG_FLAGS` allowlist of known-boolean flags, unless it has an
inline `=`. The commit's own stated rationale: "Flag arity is not recoverable from the
token... enumerating arg-requiring flags is open-ended by construction... Invert the
default instead." Scoped to the pre-pin path only (`replayable(cmd, pinned=True)` from
`_replay` skips the check). Regression tests widened per wq#2290 (probe from outside the
c/e/m population) plus a falsifiability guard that the probe input is accepted under
wq#3232's narrower scope. 11/11 pass, independently reproduced.

## What I checked

- Read `harness/acceptance.py` cold: `replayable()`, `_FLAG_TOKEN_RE`, `_ZERO_ARG_FLAGS`,
  `_command_candidates()`, `_derive_deterministic()`, `_validate_criteria()`, `derive()`.
- Ran `python3 -m pytest -q harness/tests/test_acceptance_derivation.py` → **11 passed**,
  matching the claim.
- Confirmed the exact wq#3233 shape (`pytest -k`) is now rejected pre-pin
  (`replayable('pytest -k')` → `(False, "'-k' is a dangling flag...")`), and that a
  complete/boolean-terminated command (`pytest -q harness/tests -x`, `ruff check --fix`)
  still replays. That part is genuinely fixed.
- Confirmed the `pinned=True` skip at `_replay` (line 565) is correctly wired and doesn't
  newly reject a pinned command ending in a boolean flag outside `_ZERO_ARG_FLAGS`.

## Contradiction found (CONFIRMED)

**`_ZERO_ARG_FLAGS` is one flat set shared across all 21 `REPLAY_ALLOWED_BINARIES`, but flag
arity is per-binary, not universal — the same structural flaw the commit message diagnoses
in the fix it's replacing ("enumerating... is open-ended by construction") reappears in the
opposite direction.** `-b` and `-u` are in `_ZERO_ARG_FLAGS` (added for python's boolean
`-b`/`-u`), but for `git` — itself in `REPLAY_ALLOWED_BINARIES` — both require a following
argument: `git checkout -b <branch>`, `git branch -u <upstream>`. Reproduced live against
the actual binary, not inferred:

```
$ git -C /tmp/fworker-wq3237/probe checkout -b
error: switch `b' requires a value
EXIT=129

$ git -C /tmp/fworker-wq3237/probe branch -u
error: switch `u' requires a value
EXIT=129
```

And against the shipped `replayable()` directly:

```
$ python3 -c "import acceptance; print(acceptance.replayable('git checkout -b'))"
(True, '')
$ python3 -c "import acceptance; print(acceptance.replayable('git branch -u'))"
(True, '')
```

`_derive_deterministic()` itself is not exploitable this way — it has an explicit
`if head == "git" or head not in REPLAY_ALLOWED_BINARIES: continue` guard (line ~239) that
drops every git candidate before a criterion is ever minted, so the deterministic backtick
path is safe. But `derive()`'s **model-derivation path** has no such guard: `fworker.py`
wires a live `model_call` at dispatch (`fworker.py:4121-4122`,
`acceptance_mod.derive(dict(ticket, id=ticket_id), model_call=_acceptance_model_call())`),
and `derivation_prompt()` tells the model "every criterion that CAN be a command MUST be
one" with no git exclusion. Whatever the model returns goes through `_validate_criteria()`
(line ~272), which calls `replayable(c.get("cmd") or "")` — default `pinned=False`, no
`head == "git"` check anywhere in that path. Reproduced end to end, simulating a model
that proposes a git criterion for a ticket that mentions cutting a branch (a plausible,
non-contrived derivation prompt output — nothing in `derivation_prompt()` steers the model
away from git):

```
$ python3 -c "
import acceptance
criteria = [{'id': 'AC1', 'text': 'cut the new branch', 'proof_type': 'test',
             'cmd': 'git checkout -b', 'expect_exit': 0}]
kept, rejected = acceptance._validate_criteria(criteria)
print('kept:', kept)
print('rejected:', rejected)
"
kept: [{'id': 'AC1', 'text': 'cut the new branch', 'proof_type': 'test', 'cmd': 'git checkout -b', 'expect_exit': 0, 'timeout_s': 900}]
rejected: []
```

The criterion is **kept**, not rejected — it gets pinned into `acceptance.json` and will
fail on replay with exit 129 against a pinned `expect_exit: 0`, no matter what the worker
does. This directly falsifies `derive()`'s own docstring invariant: "Anything it returns is
validated and any criterion the harness could not replay is dropped — a model that invents
`pytest tests/does_not_exist.py` costs a rejected criterion, never an unrunnable gate."
`git checkout -b` / `git branch -u` are exactly "an unrunnable gate" that this fix, by its
own stated contract, should have dropped and did not.

This is the wq#2290 falsifiability trap again, on the opposite side of the same bug class:
the widened regression tests (`test_wq3235_dangling_flags_outside_the_c_e_m_set_are_rejected_too`,
`test_wq3235_boolean_trailing_flags_and_inline_values_still_replay`) probe six non-c/e/m
arg-requiring flags and five boolean/inline-value commands, but every one of the eleven
probe commands starts with `pytest`, `node`, `mypy`, `go`, `npm`, `ruff`, `python3`, or
`curl`/`rm` (the security-shape assertions) — **none probes `git`, and none probes whether
a flag in `_ZERO_ARG_FLAGS` is actually boolean for the specific binary it's attached to.**
The new tests confirm the fix's own happy path; they don't falsify the assumption a flat
cross-binary allowlist makes.

## What I did not find broken

- The exact wq#3233 shape (`pytest -k` and five siblings: `pytest --maxfail`, `node -r`,
  `mypy --config-file`, `go -tags`, `npm --prefix`) is genuinely fixed — no test-type AC1
  is minted for any of them via the deterministic path, matching the shipped claim.
- `pinned=True` scoping at `_replay` is correct: it doesn't reject a pinned criterion whose
  worker-authored command happens to end in a boolean flag `_ZERO_ARG_FLAGS` doesn't list,
  and a genuinely dangling pinned command still fails on its own real exit code.
- No regression in the six pre-wq#3235 tests; all 11 pass, independently reproduced.
- I did not find a way to reach the git-checkout-`-b` gap through `_derive_deterministic()`
  — the explicit `head == "git"` guard there holds. The gap is real only on the
  model-derivation path.

## Action taken

Contradiction posted to wq#3235 via `wq note`. Not re-implementing `replayable()`/
`_ZERO_ARG_FLAGS` per ticket instruction ("Do NOT re-implement; verify"). Escalating per
ticket policy to opus-5@high judgment (new ticket, deps=wq#3235). Fix shape for whoever
picks it up: either (a) key `_ZERO_ARG_FLAGS` per-binary (a `{binary: {flags}}` table
instead of one flat set — `-b`/`-u` boolean for python, arg-requiring for git), or (b) add
back the `head == "git"` (or a full per-binary arity table) guard to the model-derivation
path in `_validate_criteria()`, matching what `_derive_deterministic()` already does — and
widen the regression tests to include at least one `git` probe and one flag that is
boolean for one allowlisted binary but arg-requiring for another, so the suite can actually
falsify a flat cross-binary set the way wq#2290's post-mortem asked for.
