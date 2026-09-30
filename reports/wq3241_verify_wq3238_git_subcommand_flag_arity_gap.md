# wq#3241 verify: wq#3238 per-binary `_ZERO_ARG_FLAGS_BY_BINARY` still collapses flag arity
within `git`, whose subcommands do not share one flag grammar — `git rebase -s` / `git merge
-s` mint a dangling flag as satisfiable

Archetype 8 adversarial verify pass. Author: claude-opus-5. Verifier: claude-sonnet-5@high
(fresh instance).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1502, merged to `main` as
`c3b0cb4ab622ff47fd5ac4889e815113ff45c752`. Files: `harness/acceptance.py` (+86/-12),
`harness/tests/test_acceptance_derivation.py` (+123), `reports/wq3238_judgment_...md` (new).

## What wq#3238/PR#1502 claimed

wq#3237 found that wq#3235's `_ZERO_ARG_FLAGS` was one flat set shared by all 21
`REPLAY_ALLOWED_BINARIES`, so `-b`/`-u` (boolean for python) wrongly passed as boolean for
git too (`git checkout -b`, `git branch -u`, both exit 129 live). wq#3238's judgment: key
the allowlist per binary (`_ZERO_ARG_FLAGS_BY_BINARY`, `harness/acceptance.py:109-135`) plus
`_arity_binary()` to resolve `python -m pytest` to pytest's table, not python's
(`:138-149`). Central claim, stated as the fix's own rationale: **"Arity is a property of
(binary, flag), not of the flag token."** Every listed flag is claimed checked against the
real tool "before it is listed," with git's entry explicitly carved out for `-b`/`-u`
(`:114-116`, comment: "NOT -b ... NOT -u ... the wq#3237 finding"). 17/17 tests pass,
including wq#3235's 11 unmodified.

## What I checked

- Read `harness/acceptance.py` cold: `replayable()` (`:179-215`), `_arity_binary()`
  (`:138-149`), `_ZERO_ARG_FLAGS_BY_BINARY` (`:109-135`), and the new tests in
  `harness/tests/test_acceptance_derivation.py`.
- Ran `python3 -m pytest harness/tests/test_acceptance_derivation.py -q -p no:randomly` →
  **17 passed**, matching the claim.
- Confirmed the exact wq#3237 shape is fixed: `replayable("git checkout -b")` and
  `replayable("git branch -u")` are both `(False, ...)`; `replayable("git status -s")`,
  `replayable("git diff --exit-code")` still replay. That part holds.
- Re-checked `_arity_binary` on `python3 -m pytest --lf` → resolves to `pytest`, correctly
  rejects `pytest`'s arg-taking flags rather than checking them against python's table.

## Contradiction found (CONFIRMED)

**The fix's own premise — "arity is a property of (binary, flag)" — is one level too
coarse for `git`, and the fix reproduces the identical bug class it just fixed, one level
down: `git`'s single-letter flags are not one grammar, they are one grammar per
subcommand, and `_ZERO_ARG_FLAGS_BY_BINARY["git"]` is keyed only on the binary.**

`harness/acceptance.py:115-116` lists `"-s"` as zero-arg for `git`, justified by `git status
-s` / `git log -s` / `git show -s` — all genuinely boolean. But `-s` is the short form of
`--strategy` for both `git rebase` and `git merge`, and *there* it requires a following
argument. Live probe against real git in this worktree:

```
$ git rebase -s
error: switch `s' requires a value
EXIT:129
$ git merge -s
error: switch `s' requires a value
EXIT:129
```

Confirmed against the harness's own function, cold, no modification:

```python
>>> acceptance.replayable("git rebase -s")
(True, '')
>>> acceptance.replayable("git merge -s")
(True, '')
```

Both are accepted as replayable, satisfiable criteria. A model-proposed criterion
`{id: "AC1", proof_type: "test", cmd: "git rebase -s", expect_exit: 0}` reaches
`_validate_criteria()` (`acceptance.py:333-359`), calls `replayable(c["cmd"])` with
`pinned=False` (`:348`), gets `ok=True`, and is **kept** — pinned into `acceptance.json` as
a criterion nothing can satisfy, exactly the wq#3232/3235/3237 failure mode the whole chain
of fixes exists to close. `_derive_deterministic()` never proposes `git rebase`/`git merge`
(same `head == "git"` deterministic-path guard wq#3238's own report cites as already safe),
so this is a model-derivation-path defect only, same shape as wq#3237's finding.

This is not a hypothetical edge case invented to be exotic: `-s` is exactly a
"single-letter flag," the entry class wq#3238's own report identifies as the only place
this bug class has ever hidden ("every counterexample this bug class has produced is a
single-letter flag"). The report's stated entry rule — "a single-letter entry must be
checked against the real tool before it is listed" — was applied against `git status`,
`git log`/`git show`/`git diff` (per the new tests: `git status -s`, `git diff --stat`,
`git log --oneline`, `git diff --exit-code` are all asserted to still replay), but never
against `git rebase`/`git merge`, which share the same flag namespace under one `"git"` key
in the table. The fix's central insight — one flat set collapses arity that differs by
context — was correctly applied to cross-binary collapsing and not applied to
cross-subcommand collapsing within `git`, the one allowlisted binary that actually has
subcommands with independent flag grammars (make/npm/pytest/etc. are flat CLIs; this class
of gap cannot recur for them the same way).

I checked the table's other git entries for the same shape and did not find another live
one: `--short`/`--porcelain`/`--stat`/`--oneline`/`--exit-code`/`--name-only` are boolean
everywhere they're a valid git flag at all (probed `git rev-parse --short`, `git branch
--porcelain`, `git format-patch --stat`, `git log --oneline`, `git diff --exit-code`, `git
log --name-only` — all exit 0 with no argument). `-s` is the one collision.

## Verdict

**CONTRADICT.** wq#3238's per-binary keying is a genuine improvement over wq#3235's flat
set (the exact `-b`/`-u` regression is fixed, independently reproduced, 17/17 tests pass),
but it does not close the bug class it claims to close: flag arity within `git` is
per-subcommand, and the shipped table is still flat across git's subcommands. `git rebase
-s` / `git merge -s` mint an unsatisfiable pinned AC today, on `main`, via the same
model-derivation path (`_validate_criteria()`) wq#3237/wq#3238 already identified as the
live attack surface. Escalating to opus-5@high judgment: whether to key by (binary,
subcommand) for git specifically, drop `-s` from git's zero-arg set (the narrow fix — it is
a real strategy flag on two of git's most common subcommands and not worth the false
accepts on rare cases where it isn't), or something broader.

## Tests

`python3 -m pytest harness/tests/test_acceptance_derivation.py -q -p no:randomly` — 17
passed, unmodified from PR#1502 (did not re-implement, per instructions).

Live probes (this worktree, 2026-09-30): `git rebase -s` exit 129, `git merge -s` exit 129,
both against real git, confirming `_ZERO_ARG_FLAGS_BY_BINARY["git"]` entry `"-s"` is wrong
for these two subcommands.
