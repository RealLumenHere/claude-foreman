# wq#3074 verify-pass: wq#3068 judgment (wq#3066 CONTRADICTED wq#3054, tilde gate) — CONFIRM

**Verdict: CONFIRM.** lumen-bin PR #181 (`e45b16715d`, primary `7fde3f0`) does exactly what
wq#3068's judgment writeup claims: `bad_gate_clauses()` drops the `'~'` arm, `print_bad_gate()`
stops advertising it, `gate_met()` is untouched, and the new/inverted tests hold under both the
shipped binary and an independent adversarial probe. No contradiction found.

## What was verified

- **Diff matches its own description.** Read `git show 7fde3f0` cold (`wq`,
  `tests/test_wq3033_gate_path_guard.py`, `tests/test_wq3054_gate_label_markers.py`,
  `tests/test_wq3068_gate_tilde_refused.py`). `bad_gate_clauses()`'s enforced-head check is now
  `if not head.startswith('/')` — the `or head.startswith('~')` arm is gone. `print_bad_gate()`
  no longer says "absolute or ~ path" and names the reason (`gate_met()` does not expand `~`).
  `gate_met()` itself is untouched (docstring-only change) — confirmed by `grep -n
  "startswith('~')\|expanduser" wq`: the only remaining `expanduser()` call in the file is
  `derive_deliverable()` (line 2555), an unrelated done-gate-marker subsystem, not touched by
  this PR and not part of its claim.
- **Probe run (the PR's own falsifiability evidence), reproduced independently:**
  - `tests/test_wq3068_gate_tilde_refused.py` against the live post-fix binary
    (`/home/lumen/bin/wq` @ `7fde3f0`, confirmed by `md5sum` + `git log -1 --format=%H -- wq`):
    **11 passed**.
  - Same file against the pre-fix binary (`git show 8c44a2b:wq`, PR #180's tip): **6 failed, 5
    passed** — exact match to the number the PR claims. Every refuse-case (`test_add_refuses_*`,
    `test_edit_refuses_*`, `test_the_refusal_no_longer_teaches_the_tilde_form`) fails against the
    old binary because it still accepted `'~'`.
  - `tests/test_wq3033_gate_path_guard.py` + `tests/test_wq3054_gate_label_markers.py`: **23
    passed** — both prior guarantees still hold, with `test_add_accepts_a_tilde_path_gate`
    correctly inverted to `test_add_refuses_a_tilde_path_gate`.
  - Full `tests/` (minus `test_rotate_provider_keys_swap.py`, a script-style file that
    `sys.exit`s at import, same exclusion the PR used): **12 failed, 498 passed**, and the 12
    failing test names (`test_wq_done_gate_terms.py`, `test_wq_evidence_durability.py`,
    `test_wq_hygiene_reconcile.py`) are all in an unrelated deliverable/hygiene subsystem — the
    same pre-existing-failure set the PR names.
- **Independent fact-checks, not just re-running the PR's own tests:**
  - `queue.db`: `SELECT count(*) FROM tasks WHERE gate LIKE '%~%'` → **0**, confirming "zero live
    rows carried a `~` gate" independent of the author's claim.
  - `grep` across `/home/lumen/bin`, `/home/lumen/foreman`, `/home/lumen/kb` for any script or
    doc that writes a `--gate '~...'` call in practice (not just in reports discussing this
    ticket): none found. Nothing outside the test suite depended on the old acceptance.
  - Commit provenance: `e45b16715d` is a real merge commit on `origin/main`
    (`git cat-file -t e45b16715d` → `commit`); `7fde3f0` is its content commit and is the current
    tip touching `wq` (`git log --oneline 8c44a2b..7fde3f0` → one commit). Not a dangling or
    invented SHA.
- **Adversarial probing beyond the shipped test suite** (isolated `WQ_DB`/`WQ_AGENT` scratch
  env against the live binary, not the real queue — see note below on one slip): username-form
  tilde (`~lumen/foo:exists`), tilde with nothing after the slash (`~/:exists`), bare `~` with a
  colon (`~:exists`), leading-whitespace tilde, a literal `$HOME/x:exists` (untouched — `$HOME`
  is not `~`, and this PR doesn't claim to touch it), a relative `../x:exists`, and an absolute
  path that merely *contains* `~` mid-string (`/home/lumen/notes~backup.md:exists`, the same
  case as the shipped `test_a_path_that_merely_contains_a_tilde_is_still_fine`). Every enforced
  head not starting with `/` was refused; the mid-string case was accepted past the gate check
  (it hit an unrelated dedup guard from a repeated probe title, not a gate refusal — confirmed by
  reading its stdout). No new defect surfaced.

**Process note (self-reported, wq#2795-adjacent, not a rotation event):** the first adversarial
probe run used `/home/lumen/bin/wq add --gate ... --force` directly against the *live* queue
instead of a scratch `WQ_DB`, which created a real ticket (wq#3075) in the production queue. It
was immediately blocked (`wq block 3075 "accidental ticket ... not real work"`) rather than left
open. All subsequent probing used an isolated `WQ_DB` under `/tmp/fworker-wq3074/`.

## Finding: citation points at an unmerged file (does not change the verdict)

wq#3068's judgment writeup and its notes field both cite
`reports/wq3066_verify_wq3054_tilde_gate_never_expanded.md` as the evidentiary basis ("stands in
full") for treating wq#3066 as a CONTRADICT of wq#3054. That file does not exist on `main` in
`/home/lumen/foreman` — `git log --oneline --all -- "reports/wq3066*"` finds it only in one
commit, `318290f3` ("docs(verify): contradict wq3054, tilde gate is unsatisfiable"), which lives
solely on `origin/docs/wq3066-verify-wq3054-tilde-gate`, a branch never merged to `main`.
wq#3066's own ticket record shows why: it was first auto-blocked with `UNKNOWN: FALSE_COMPLETE:
no mechanical signature matched` (`/home/lumen/foreman/_runs/wq3066/failure.json`), then later
reconciled back to `status: done` with the note pointing at `318290f3` — but reconciliation
appears to check that the commit SHA exists somewhere reachable, not that it landed on `main`.
The content of wq#3066's analysis (verified independently above via direct testing, not by
trusting the citation) holds up; what's missing is that a reader of `main`'s `reports/` directory
today cannot find the document wq#3068 says "stands in full." This is a paper-trail/reconciliation
gap in the wq3033→wq3047→wq3054→wq3066→wq3068 judgment chain, not a defect in PR #181's code, so
it does not change this ticket's verdict — but it's worth a look since it means "done" for a
verify-pass ticket doesn't currently guarantee its report landed on `main`.

## Disposition

CONFIRM. wq#3068's judgment (drop `'~'` from legal enforced `--gate` heads; do not teach
`gate_met()` to expand it) is upheld by the code, the tests, and independent adversarial probing.
No contradiction, no escalation.
