# wq#3242 judgment: git flag arity is per-subcommand — ban git's short flags, in code

wq#3241 (verify of wq#3238/PR#1502) returned CONTRADICT: `_ZERO_ARG_FLAGS_BY_BINARY["git"]`
listed `-s` as zero-arg, true for `git status`/`log`/`show` and false for `git rebase`/`git
merge`, where `-s` is `--strategy` and requires a value. Reproduced live in this worktree
(2026-09-30): both `git rebase -s` and `git merge -s` print ``error: switch `s' requires a
value``. `replayable("git rebase -s")` returned `(True, "")` on main, so a model-proposed
criterion ending in either was pinned as an AC nothing can satisfy.

## Decision

Both of the ticket's narrow options, plus the part neither of them covers.

1. **`-s` is dropped from git's set** (`harness/acceptance.py:138-139`). The capability lost
   is nil: `--short`/`--stat`/`--oneline` carry the same query criteria in long form and stay
   listed. The false accept it bought was a permanently unsatisfiable pinned AC — a rework
   loop, the most expensive failure this gate has.
2. **The invariant moved out of the table** (`:164`, `:231-235`). For a binary that
   dispatches to subcommands, `replayable()` now rejects every trailing single-dash flag
   outside the help flags, whatever the table says. `_SUBCOMMAND_BINARIES` = git, go, cargo,
   npm, npx, pnpm, yarn. Help flags stay exempt for the reason already recorded at
   `_HELP_FLAGS`: no CLI takes an argument to its help.

**Why not (binary, subcommand) keying.** It is sound — the default is still reject, so an
unlisted pair costs a citation criterion, not a wrong accept. It is also the fourth
consecutive fix of the same bug class (wq#3232 → 3235 → 3237 → 3238 → this) that would have
left the invariant living in a hand-maintained table. Per-subcommand keying makes that table
an order of magnitude larger, puts every future entry back on the honour system of "was this
pair probed against the real tool", and buys only rare boolean short flags (`git status -s`,
`git show -s`) whose long forms already work. The structural ban ends the class instead:
`git merge -s` cannot come back through a table edit.

**Scope check.** No short flag beyond `-s` was listed for any subcommand binary, so the ban
costs nothing else today. wq#3241 checked git's long-form entries
(`--short`/`--porcelain`/`--stat`/`--oneline`/`--exit-code`/`--name-only`) against real git
across subcommands and found no second collision; long-form predicate switches have no value
grammar to get wrong, which is why the ban is short-flag-only. Flat CLIs keep their short
flags — `pytest -x`, `make -k`, `python3 -V` all still replay; collapsing those was wq#3237's
finding and stays fixed.

One adjacent defect found and deliberately left: `git -V` is still accepted (`-V` is a help
flag, hence exempt from the ban), but real git rejects it — "unknown option: -V", exit 129,
probed live. That makes an AC ending in it unsatisfiable for a reason this check does not
police: recognition, not arity, an exclusion the `_HELP_FLAGS` comment already records as the
gate's business rather than the derivation's. Recorded here rather than widened into, since no
derivation path proposes it and `git --version` is the form that gets used.

## Tests

`python3 -m pytest harness/tests/test_acceptance_derivation.py -q -p no:randomly` — 20
passed (17 from PR#1502 + 3 new).

- `test_wq3242_git_dash_s_is_dangling_because_rebase_and_merge_read_it_as_strategy` — the
  regression, plus the accepted cost (`git status -s` rejected, `--short` still replays),
  plus flat-CLI short flags untouched, plus the model derivation path dropping the criterion.
  `rebase`/`merge` are outside the population git's entry was built from — the wq#2630 guard.
- `test_wq3242_the_invariant_does_not_live_in_the_table` — re-adding `-s` to the table does
  not resurrect `git merge -s`, and emptying `_SUBCOMMAND_BINARIES` does not either. Both
  layers must fail at once.
- `test_wq3242_the_probe_really_can_fail_against_the_wq3238_table` — lift both layers and
  `git merge -s` is accepted and survives `_validate_criteria()` into a pinned AC: the defect
  as it merged. Restored, both probes flip.

`harness/tests/test_evidence_gate.py` has 4-5 failures in this worktree that are unrelated:
the set of failures changes with cwd (4 from the worktree, a different 5 from `/tmp`) because
`stop_gate.decide()` resolves the worktree from `FOREMAN_WORKTREE_ROOT`/cwd
(`harness/hooks/stop_gate.py:68-69`) and picks up the live worker session's own pinned
acceptance.json. Nothing in this diff touches `stop_gate` or the pinned path; the fixtures
here use `python3 probe.py`, which has no trailing flag.
