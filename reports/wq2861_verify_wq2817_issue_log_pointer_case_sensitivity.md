# wq#2861 — verify-pass of wq#2817 (PR#1175, `00fe1ef`/`ops/wq2817-issue-log-pointer-integrity/`): CONTRADICT

Verifier: claude-sonnet-5@high, fresh instance. Date 2026-09-28. No credentials were read,
printed, or constructed anywhere in this verification; the repro fixtures are synthetic
row numbers, not secret-shaped text.

## Claim under test

wq#2817 (PR#1175) ships `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`: a
checker that generalises wq#2763's single finding (kb/ISSUE-LOG.md row 824 pointed readers
at row 834 instead of 835) into three checks run over **every row** of the log — `DUP`
(a row number defined twice), `DANGLING` (a `see row N` citing a target that doesn't
exist), and `ONE-WAY` (a `see row N` correction pointer where the target never cites the
citing row back). The PR's own report (`reports/2817_judgment_wq2763_contradicted_wq2740_row_824_pointer.md`)
states the point of generalising was that "a one-row edit verified by re-reading that one
row is a probe that cannot fail" (wq#2630) — the checker is supposed to be able to catch
this class of defect anywhere in the corpus, not just at row 824.

**Important scope note found during this verify-pass**: PR#1175's commit `00fe1ef` is
*not yet merged* (`git merge-base --is-ancestor 00fe1ef main` → false), but its content is
byte-identical to commit `3e13d42` ("fix(kb): add an issue-log pointer-integrity probe",
also `Refs: wq#2817`), which **is** already an ancestor of main today (landed via a
different worktree/lane, `feat/wq2820-...`). `git diff 3e13d42 00fe1ef -- ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`
produces no output. So the code under test — bugs and all — is already live on main,
independent of whether PR#1175 itself ever merges.

## What holds

- Ruling 1 (row 824 pointed at the wrong row) and ruling 2 (835, not 836, is correct):
  confirmed directly against the live `kb/ISSUE-LOG.md` — row 824's status cell now reads
  "...see row 835" (`kb/ISSUE-LOG.md:948`), row 835 (`:960`) opens "row 824 above closes
  rotation row...", and row 834 (`:958`) is a different chain (wq#2739 on wq#2737/wq#2731),
  matching the PR's claim that 834 was the wrong target.
- Ruling 3 (kb now has a committable path, wq#2759 closed): `bin/session-handoff:34,253`
  does fold `ISSUE_LOG_PATH` into the timer's `git_commit()` call as claimed.
- All 5 shipped tests pass unmodified: `python3 -m pytest -q harness/tests/test_wq2817_issue_log_pointers.py`
  → `5 passed in 0.52s`.
- Run cold against the live corpus (`--path /home/lumen/kb/ISSUE-LOG.md`, 839 rows): no
  `row 824` finding of any kind — the specific defect wq#2763 found is genuinely fixed and
  stays fixed under the shipped checker.

## Evidence the checker fails open (contradiction)

`probe_pointers.py`'s `SEE_RE = re.compile(r"see rows?\s+(\d+)")` has no `re.IGNORECASE`
and the input lines are never lowercased before matching. A `see row N` citation that
starts a new sentence — ordinary English capitalizes it "See row N" — never matches the
pattern, so it is silently invisible to all three checks (`DUP`, `DANGLING`, `ONE-WAY`).
This is not a hypothetical: **the corpus this checker exists to guard already contains
this exact shape right now.** `kb/ISSUE-LOG.md:104` (row 80) reads:

> "...it was landed rather than discarded. See row 109 for the mechanics."

Reproduced independently, importing the shipped module directly (no reimplementation):

```
$ python3 /tmp/fworker-wq2861/probe_case_sensitivity.py
lowercase 'see row 999'  -> ["DANGLING row 5 (L1) -> 'see row 999': no such row"]
capitalized 'See row 999' -> []
CONFIRMED: capitalized citation is silently invisible to the checker
live occurrences of 'See row 109' in kb/ISSUE-LOG.md: 1
```

The lowercase form of an identical dangling citation is correctly caught (`DANGLING row 5
... no such row`); the capitalized form of the exact same citation produces `[]` — no
finding at all. Full script: `/tmp/fworker-wq2861/probe_case_sensitivity.py`.

In the live corpus, row 80's own citation happens not to produce a *reported* defect once
capitalization is fixed either (row 109 is itself a pre-existing `DUP` — flagged
separately — and one of its two entries, `kb/ISSUE-LOG.md:117`, does mention "row 80"
back), so fixing the regex would not change today's row-80 finding count. That is beside
the point: the checker's own stated design is to catch **every** row's citations, and it
currently does not look at every row's citations — it only looks at rows that happen to
spell the citation in lowercase. Sentence-initial capitalization is not a rare style
choice; it is how the rest of this very log writes prose. A future worker who mistypes a
capitalized "See row N" the way wq#2740 mistyped its lowercase one — the exact defect
class wq#2817 was written to generalise against — passes this checker silently. The
regression tests shipped with the PR (`harness/tests/test_wq2817_issue_log_pointers.py`)
only exercise lowercase `see row` fixtures, so this gap has zero coverage on either side.

## Secondary finding: no `--verify` handler (independent reason this couldn't be confirmed)

The ticket names this directly: `ops/wq2817-issue-log-pointer-integrity/` has no
`install.sh` at all (`ls` shows only `probe_pointers.py`), so there is no `--verify`
handler per `ops/VERIFY_CONTRACT.md`. Per that contract and wq#1696, "a repo-only check
does not count." I did not author one: the bundle installs nothing anywhere (no
`/home/lumen/bin` copy, no systemd unit — the script is meant to be invoked in place via
its repo path per its own `Usage:` docstring), so there is no "live target" for a
`vfile`/`vunit_file`/`vactive` check to compare against in the shape every reference
bundle (`wq900-primary-tree-guard`, `wq1658-cron-stamp-dropin`, `wq1609-minerva-contradiction-scan`)
uses — deciding what "live" should mean for a repo-invoked-in-place checker is a scope
call I'm leaving to the judgment ticket rather than re-implementing past this verify-pass's
remit. Noted here because it means this PR could not have been confirmed via bundle
liveness even before the regex bug above.

## Disposition

CONTRADICT. The specific wq#2763 finding (row 824 → 835) is genuinely fixed and the fix's
own regression tests hold, but the generalised checker's core claim — that it checks every
row's `see row N` citations, not just the one wq#2740 mistyped — is false for any citation
that isn't written in all-lowercase, which is not a rare shape (row 80, today, in the
corpus this checker guards). The defective code is already live on main via `3e13d42`,
so this is not merely "don't merge PR#1175 as-is" — the gap exists in production today.
Not re-implemented (per this ticket's instructions): the fix is a one-line change
(`re.IGNORECASE` on `SEE_RE`, or `.lower()` the line before matching), but sits with the
judgment ticket to decide whether it lands as a patch to the already-live `3e13d42` code,
a revision of PR#1175, or both, and to decide the `--verify` handler's shape. ISSUE-LOG
row 884. Escalating as a `[judgment]` ticket for claude-opus-5@high per this ticket's
contradiction protocol.
