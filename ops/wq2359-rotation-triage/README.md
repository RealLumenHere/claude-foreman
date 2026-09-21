# wq#2359 — the 47-ticket rotation cascade, triaged as one batch

The wq#2275 tripwire filed 47 `[rotation] burned credential` tickets and 44 `[verify]`
followups between 2026-09-20 22:09 and 2026-09-21 03:26. Five independent
investigations had already returned "no credential was burned" (ISSUE-LOG rows 577-581)
at roughly 10 USD of xhigh worker each. This is the same question asked once, of all 47
rows, by `harness/mask_triage.py`.

## The command

    python3 harness/mask_triage.py 2288 2289 2290 ... 2400

Two or more ticket ids triage as a batch: one summary line per ticket, one read of the
tracked tree, one load of each run's corpus, one load of each `harness/masking.py`
revision. The 47 rows share 16 runs and 5 rule tables, so the batch does 16 corpus
loads instead of 47 and one tree scan instead of 47.

Every ticket is still scored the row-581 way, and nothing about the verdict is relaxed:
the rule table live at the ticket's `created_at`, `burn_filter.classify()` making the
call, a random never-committed `ghp_`+36 planted first and required to come back
UNACCOUNTED, and output that is label / prefix / length / 12 hex of sha256 — never a
value.

## What it found

47 tickets, 0 credentials burned, nothing rotated.

    47 tickets: 43 clean, 4 to rotate, 0 unresolved, 0 control-failed

34 tickets cleared against HEAD's tree alone: every credential-shaped value in the run's
model context is committed text, or a string HEAD's table no longer reads as a
credential. 13 more did not, and those held 16 distinct values over runs wq#2273,
wq#2278, wq#2289, wq#2290 and wq#2299.

**5 of the 16 are committed text in a revision that is no longer HEAD**, which clears 9
of the 13 tickets. The runs read a worktree that was behind main: the 40-char
`secret = "` value that wq#2278, wq#2290 and wq#2299 all paged on is
`harness/tests/test_token_injection.py` as committed at `c48054b`..`830ec12` and cut by
`345bf49`, and the four `sk-` values are `token-engine` fixtures from `421bd8a`. The
history index accounts them by hash, and the report names the blob that did it
(`accounted: history:<blob>`).

The remaining **4 tickets — wq#2300, wq#2302, wq#2317 (run wq#2289) and wq#2301 (run
wq#2273) — still read ROTATE, and were settled one at a time**, on the record rather
than by widening the tool:

* **Run wq#2289's 10 values are synthetic test vectors wq#2289 typed itself**, into a
  Bash snippet and an `Edit` new_string while drafting mask tests it then reverted:
  `('PAT = "github_pat_<26 chars>", True)` and `('SECRET: <67 chars>', True)` sit in a
  `cases = [...]` list beside `('PASSWORD=MASKED', True)`, and `"TOKEN = <16 chars>"` in
  a `for text in [...]` list beside its own `"TOKEN = MASKED"` control. Eight are named
  by hash in ISSUE-LOG row 598; the last two (`b6dfb0aa3bca`, `f39dac6cbaba`) are on the
  record here, each read from `mask_replay`'s own masked context window. They stay
  UNACCOUNTED on purpose: a real token pasted into an `Edit` is burned, so the tool must
  not learn to dismiss "the worker typed it".
* **Run wq#2273's one value is a line of `/home/lumen/bin/lumen-github-identity.sh`**, a
  tracked file of the `/home/lumen/bin` repo — hash-matched there under the same two
  rule tables while triaging this batch. `mask_triage` scores against the foreman tree,
  so a value that is source in a sibling repo is invisible to it.

Six of run wq#2289's values and run wq#2273's value are also in blobs that exist in this
repo's object store but are reachable from no ref — a dropped commit or a bare `git
add` in some worker's worktree. The index deliberately does not count those: text a
reviewer cannot open is not the "committed text" the provenance screen means. That is
the whole difference between this run's 4 ROTATE rows and the 3 an all-objects index
reports.

## Why the history index is not a wider excuse

Accounting a value against an older revision widens what counts as committed text, and
a wider tree is a wider excuse. Two guards: the control is re-planted and re-run over
the widened tree before any verdict is read from it (a tool that cannot report a burn
prints "clean" for everything), and the index is only built when a first pass against
HEAD's tree leaves something unaccounted — a clean ticket never pays for it, and a
cleared value names the blob that cleared it.

## Files

* `harness/mask_triage.py` — batch mode, history accounting, `--no-history`.
* `harness/tests/test_mask_triage.py` — 29 tests, including the batch tally, the
  one-index-per-batch guarantee, and a value no revision ever held still burning.
  192 passed, 1 skipped across the six mask suites at HEAD.
* ISSUE-LOG row 618 — the cascade's verdict and the two accounting gaps behind it.
