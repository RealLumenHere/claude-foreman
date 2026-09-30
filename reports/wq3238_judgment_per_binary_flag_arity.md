# wq#3238 judgment: flag arity is keyed per binary, not per flag

wq#3237 escalated a judgment call rather than picking a fix: is excluding git's `-b`/`-u`
the right answer, or does the defect need a full per-binary arity table — which the fix's
own rationale calls open-ended, since we do not have 21 tools' declared flag schemas?

## Judgment: per-binary keying (option a), and it is not open-ended

The dichotomy in the ticket is false, and that is the whole finding. "A full per-binary
arity table" sounds unbounded only if the table has to be **complete** to be sound. It does
not, because wq#3235 already inverted the default to **reject**: a flag outside the
allowlist is dangling, the candidate is dropped, and the ticket falls back to the citation
criterion. Keying that allowlist per binary inherits the same asymmetry. An entry that is
missing costs one candidate; an entry that is wrong pins a gate nothing can satisfy. So the
table can be partial, grow by evidence, and still never accept an unsatisfiable criterion.

Option (b) — restoring a `head == git` guard on the model-derivation path — was rejected.
It fixes the one reported instance and leaves the class live: `-b`/`-u` are not special,
they are simply the first two tokens someone noticed are boolean for one allowlisted binary
and argument-requiring for another. A `git` exclusion also mis-states the reason: git is not
the problem, a flat set is. And it would make the model path *less* capable than it needs to
be — `git diff --exit-code` is a legitimate machine-checkable criterion, and under (b) it
would be thrown out along with `git checkout -b`. Per-binary keying rejects exactly the
unsatisfiable one and keeps the satisfiable one (both asserted in the new tests).

## What shipped

`harness/acceptance.py:109-135` — `_ZERO_ARG_FLAGS` becomes `_ZERO_ARG_FLAGS_BY_BINARY`,
`{binary: {flags}}`. No universal fallback: a binary absent from the table has no boolean
flags at all, so the default really is reject. `_HELP_FLAGS` is composed **into** each
entry explicitly (opt-in), not consulted as a fallback.

`harness/acceptance.py:138-148` — `_arity_binary(argv)` resolves the binary whose schema
governs the trailing token. `python3 -m pytest --lf` is parsed by pytest, not python;
keying on `argv[0]` would check `--lf` against the interpreter's table and reject a
complete command. `is_whole_suite()` already unwraps the same shape.

`harness/acceptance.py:199-205` — `replayable()`'s call site, still pre-pin only, now
naming the owning binary in the rejection reason (`"-b" is a dangling flag for 'git'`), so
rework.json says which parser disagreed.

### Entry rule, from the evidence rather than taste

Every counterexample this bug class has produced is a **single-letter** flag: `-c`/`-e`/`-m`
(wq#3232), `-k` (wq#3233), `-b`/`-u` (wq#3237). That is where arity is binary-specific. So a
single-letter entry must be checked against the real tool before it is listed; a long-form
`--switch` whose name is already a predicate (`--dry-run`, `--noEmit`, `--porcelain`) has no
value grammar to get wrong and is listed on convention. This gives the table a termination
condition, which is what the "open-ended" worry was really about.

### Two entries removed on the same evidence

`python`'s `-i`/`-u`/`-b`/`-E` are genuinely boolean and are still gone. A command *ending*
in one is a bare REPL invocation, and `_replay()` does not close stdin
(`harness/acceptance.py:578`), so it would sit until the replay budget expires rather than
pass. Unsatisfiable by hanging is no better than unsatisfiable by arity, and the trailing
position is the only position this check ever judges.

## Live arity evidence (Lumen, 2026-09-30)

Probed argv-style with stdin closed against the real binaries, in a throwaway git repo:

```
git checkout -b   exit=129     git status -s      exit=0
git branch -u     exit=129     git diff --stat    exit=0
                               git log --oneline  exit=0
                               git diff --exit-code exit=0
make -k           exit=2   "No targets specified and no makefile found"   <- no arg consumed
pytest -k         exit=4   "argument -k: expected one argument"            <- arg required
```

`make -k` / `pytest -k` is the pair a flat set cannot express: one token, two arities,
both real. It is now the cross-binary probe in the regression suite.

## Falsifiability (wq#2290)

wq#3235's suite probed 11 commands, none git, none testing a flag against a second binary —
it could not have caught this. The new guard
(`test_wq3238_the_probes_really_can_fail_against_a_flat_set`) re-creates the defect twice
and requires the probes to flip:

- **(a)** `WQ3235_FLAT_SET`, the set that actually merged in PR#1500, kept verbatim in the
  test: `git checkout -b` and `git branch -u` are accepted, and `git checkout -b` survives
  `_validate_criteria()` — the merged defect, reproduced, not paraphrased.
- **(b)** the new per-binary table collapsed into one cross-binary set: `pytest -k` is
  accepted, because make's boolean `-k` leaks onto pytest. This is the bill for widening a
  flat set instead of keying it, and it is what re-flattening the table in future will cost.

`test_wq3238_the_model_derivation_path_drops_the_git_criterion` probes the path the bug
actually lived on — `derive(model_call=...)` → `_validate_criteria()` — not just
`replayable()`, and asserts the docstring invariant it falsified: the git criterion is
dropped, the pytest one kept.

## Out of scope, filed separately

1. **wq#3239** — `_replay()` inherits the harness's stdin
   (`harness/acceptance.py:578`), so any pinned criterion that reads stdin blocks for the
   full replay budget. Dropping python's REPL flags removes the *derivation* path to it;
   closing the hole itself is a one-line `stdin=subprocess.DEVNULL` with its own probe.
2. **wq#3240** — `_validate_criteria()` will keep a *well-formed* mutating git command
   (`git checkout -b feature`) as a criterion the harness then runs with `cwd=worktree`.
   Arity is not the issue there; a gate that can write to the branch it judges is.

## Not caused by this change

`harness/tests/test_evidence_gate.py`'s five `stop_hook` tests fail at HEAD (536c5d63) in a
pristine worktree with no local changes — they read the ambient `FOREMAN_*` environment a
worker session exports and resolve the live ticket's run dir instead of the fixture's.
ISSUE-LOG row 1118.

## Tests

`python3 -m pytest harness/tests/test_acceptance_derivation.py -q -p no:randomly` — 17
passed, including all 11 of wq#3235's, unmodified.
