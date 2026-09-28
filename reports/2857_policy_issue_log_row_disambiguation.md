# wq#2857 — policy: disambiguating kb/ISSUE-LOG.md's duplicate row numbers

Worker: BUILD worktree `/home/lumen/worktrees/wq2857-a1`, 2026-09-28.

## The backlog, as of this ticket

`ops/wq2817-issue-log-pointer-integrity/probe_pointers.py` against the live file reports
128 findings (`git -C /home/lumen/kb log -1` → `05ab8551`): 127 row numbers defined more
than once (two of them — 714, 770, 788, 884 — three times each) plus the pre-existing
one-way pointer, row 164 → "see row 38". wq#2817's own baseline at rev `10e77b2` was 124;
the growth since is exactly the failure mode this ticket exists to catch (row 869: wq#2787
and wq#2836 landed the same number the same day; row 885 landed while this ticket was open).

## Decision: no straight renumbering, ever

Renumbering an existing row changes the string every prior citation of it depends on —
`grep -rn 'row 836'` today returns nothing, but that's true of exactly one duplicated
number, checked by hand; it is not a property the other ~60-odd duplicated numbers have
been checked for, and a bulk renumbering script has no way to also rewrite prose citations
sitting in `reports/*.md`, commit messages, or another lane's in-flight ISSUE-LOG edit.
Renumbering trades a machine-detectable defect (the DUP finding) for a silent one (a
citation that now points at the wrong row and gives no error). That is a worse failure
mode than the one this ticket is fixing.

## Decision: disambiguate by suffix on the later occurrence, not by touching the earlier one

When a duplicate is deliberately cleaned up (not in this ticket — see "Scope" below), the
row that was appended **second** (higher line number, i.e. the newer entry that caused the
collision) gets a lowercase-letter suffix on its row number cell (`836` → `836b`); the
first occurrence keeps its bare number unchanged. This means:

- Every existing citation of the bare number still resolves to the row it always meant
  (the first occurrence) — nothing needs to be found and rewritten.
- The suffixed row is trivially greppable as new (`row 836b`) without colliding with the
  original.
- It's additive: one cell edit per row, not a cross-file rewrite.

This requires widening `probe_pointers.py`'s `ROW_RE`/`SEE_RE` (currently `\d+` only) to
accept an optional trailing lowercase letter, so a suffixed row is still tracked as a row
(and not silently invisible to future DUP/DANGLING/ONE-WAY checks). That regex change,
plus the actual per-row edits, is deliberately **not** done in this ticket: it means
editing kb/ISSUE-LOG.md content across ~60 collision sites in the live, doctrine-critical
file every worker reads, which needs its own careful pass and its own probe run before/after
— not a rider on the ticket that builds the detector. The policy is recorded here so that
cleanup ticket doesn't have to re-litigate it, and so a probe/regex change lands with a
citation-preserving policy already settled, instead of improvising one under time pressure.

## Decision: prevent new collisions going forward with a detector, not a style rule

A "please check for the next free row number before appending" convention is exactly the
kind of care wq#2763 already showed doesn't scale — wq#2787 and wq#2836 collided on row
869 the same day despite it. `ops/wq2857-issue-log-hygiene-alarm/` (this ticket) wires
`probe_pointers.py` into a 30-minute timer that diffs live findings against a pinned
baseline (`known-findings-baseline.json`, 128 findings, captured at this ticket) and
alarms only on a finding beyond that baseline — i.e. exactly a new collision or a newly
mistyped pointer, caught in one timer cycle instead of surfacing days later in a
downstream ticket. See `ops/wq2857-issue-log-hygiene-alarm/README.md` for install and
rebaseline instructions.

## Amendment (wq#2894): the suffix namespace already had an occupant

Older rows write `row 206b` to mean *item (b) of row 206* — row 206 ends in a
"FLAGGED for the orchestrator: (a) … (b) …" list, and row 220 cites its (b), the fleet
password rotation, as `row 206b`. Under the suffix convention above that same string
now reads as "the second row numbered 206", which is an unrelated row (guard.sh's rm-rf
false positive). wq#2894's widened checker found it as a dangling pointer and the
citation was rewritten to `row 206 item (b)`.

So: an item-letter reference must be written `row N item (b)`, never `row Nb`. The
suffix form means a disambiguated row and nothing else.

wq#2894 also found the reverse error, and it is the more expensive one: wq#2891
suffixed row 889 → `889b` on a stale `DUP:889:2` baseline key that the probe already did
not report at kb `4892cf3`. There was no duplicate to disambiguate, so the rename simply
deleted the row number two live citations (rows 891, 892) depended on — the exact failure
mode the "no straight renumbering, ever" decision above exists to prevent. Before
suffixing a row, re-run `probe_pointers.py` against the current kb tip and confirm the
`DUP:<n>:2` key is live *now*; a baseline key is a record of the past, not a work order.

## Scope note

This ticket's deliverable is the alarm (`ops/wq2857-issue-log-hygiene-alarm/`,
`ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py`) plus this policy. It does
not renumber or suffix any of the 128 existing findings in the live `kb/ISSUE-LOG.md` —
that's the follow-up the policy above is written for.
