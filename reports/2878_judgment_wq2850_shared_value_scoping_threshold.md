# wq#2878 — judgment: wq#2850's `>=2 sharers` scoping threshold

Ruling on the question wq#2874 escalated after CONTRADICTING PR#1188 / `77c6cbefc6`.
Written 2026-09-28, opus-5@high per the archetype-8 escalation rule.

## Ruling: NOT working-as-intended. The escalation is upheld and the fix is shipped here.

The `>=2` parenthetical the ticket contains is one clause of item (1). The ticket also
carries item (2) as a **separate, unconditional requirement**:

> (2) When a closed value is still OPEN on another subject, never flag lines that do not
> name the closed subject.

"another subject" is singular — N>=1 — and item (2) is not written as a consequence of
item (1)'s derived set. PR#1188 collapsed the two: it computed item (1)'s census set and
then used it as the *only* gate on item (2)'s behaviour
(`scoped: … or val in shared_values`). So the shipped fix satisfies item (1)'s literal
threshold and fails item (2) outright. This is not a class-level-goal argument that could
be waved off as scope creep — it is a requirement the ticket enumerated and the diff does
not implement. WAI is not available as a ruling.

## What I re-verified independently (not taken on wq#2874's word)

| Claim | Status | Evidence |
|---|---|---|
| `scoped` is gated on a `len(subs) >= 2` census | confirmed | pre-fix `lumen_contradiction_scan.py:336, :371` (this branch rewrites both) |
| `scoped` requires subject **and** predicate cue before a hit | confirmed | `:446-451` — so scoping tightens attribution, it does not disable the scan |
| N=1 reproduces wq#2812 unchanged through the patched path | confirmed | baseline probe below: `scoped=False`, 1 hit on `kernel: '7.0.0-30-generic'` |
| shipped regression test hard-codes N=2 | confirmed | `test_lumen_contradiction_scan.py:166-171, :250-254` (nephthys+thoth) |
| N=2 case does work | confirmed | baseline probe case 3 PASSes |
| a unique self-identifying value still matches unscoped | confirmed | `thoth.tailscale_ip` probe case 5 PASSes on both sides |

## One thing wq#2874 understated: the census is empty in the steady state

wq#2874 framed the gap as a boundary-off-by-one at N=1. It is wider than that. The census
counts subjects **currently open** on the dead value, so it decays to zero exactly when
wq#2850's "every kernel bump manufactures these" prediction comes true: once the rollout
finishes and every node has closed `-30`, no subject is open on it, `shared_values` drops
the string entirely, and every doc quoting `-30` is unscoped and mis-attributed to
whichever node's closed fact the index reaches first. A pure census fix is therefore
correct only during the window where the fleet is mid-rollout — the narrowest slice of the
value's life. Baseline probe case 2 demonstrates this: N=0, `scoped=False`, 1 hit.

That is what decides the fix shape between the two options wq#2874 offered. Lowering the
threshold to `>=1` alone would leave the steady state broken, so this branch does both:

1. **Census, corrected to item (2)'s wording** — `open_subjects_by_value[val] - {subj}`,
   i.e. any *other* subject still open on the value, no count threshold
   (`lumen_contradiction_scan.py:375-378`, census built at `:334-340`, consumed at `:387`).
   Subtracting `subj` is what makes `>=1` safe: a value still open on the closed fact's own
   subject is a reopen/rename, already handled at `:362` and `:370-374`, not evidence of
   sharing.
2. **Predicate-aware floor for the classes the ticket named** — `kernel`, `os`, `distro`,
   `bios`, `firmware`, `model`, and any `*_version`, scoped regardless of census
   (`SHARED_VALUE_PREDICATE_RE`, `:114-125`). The ticket's "rather than a hand list"
   objection was to hand-listing which *values* are shared; this lists no values. It says
   a kernel build is fleet vocabulary *by construction*, which is the root cause the ticket
   itself states, and unlike the census it does not depend on ingestion completeness or on
   where in a rollout the fleet happens to be.

The cost is recall: a stale `kernel:` line that names no node is no longer flagged. That
trade is already the accepted behaviour at N>=2, and the alternative is a hit whose
`subject` field is a guess — which is the complaint wq#2812 filed.

## Falsifiable evidence

Probe `/tmp/fworker-wq2878/probe_threshold.py` (scratch; loads a module by path, builds its
own fixture DB from wq#2812's values). Baseline = `HEAD:…/lumen_contradiction_scan.py`
(wq#2850 as shipped), treatment = this branch. Two positive controls must flag on both
sides, so the probe can fail in either direction:

| case | baseline | treatment |
|---|---|---|
| N=1 sharer, kernel, no subject named (wq#2812's shape) | FAIL: hits=1 | PASS: hits=0 |
| N=0 sharers, kernel, no subject named (post-bump steady state) | FAIL: hits=1 | PASS: hits=0 |
| N=2 sharers, kernel, no subject named (shipped test's shape) | PASS: hits=0 | PASS: hits=0 |
| N=1 sharer, kernel, **subject named** (control: must flag) | PASS: hits=1 | PASS: hits=1 |
| N=0 sharers, **non-shared predicate** (control: must flag) | PASS: hits=1 | PASS: hits=1 |

Committed equivalents, which fail on the pre-fix module for the same reasons:
`test_lumen_contradiction_scan.py:174-229` — N=1 scoping, N=1 positive control, N=0
predicate-class floor with its own positive control, a 14-case predicate-pattern table
(including `osd_count`/`hostname` as non-matches, i.e. the pattern's own boundary), and a
no-over-suppression check on a unique IP. Suite: 75 passed, was 57.

## Not done, and why

- **wq#2850 item (4), corpus before/after hit counts.** Still not run — by either pass.
  It needs `/home/lumen/minerva/minerva.db` and the doc corpus, both outside this worker's
  `--add-dir` bounds; the read is refused on the path, not on the command. Filed as
  **wq#2883** (ops lane, which does have that reach).
- **The fix has never been live.** `/home/lumen/bin/lumen-contradiction-scan` still lacks
  wq#2850 entirely (`diff` against `HEAD` shows only the wq#2850 hunks missing), so the
  timer has been scanning with pre-wq#2850 code since PR#1188 merged: `install.sh` was
  never re-run. Both wq#2850's and this branch's change land in the repo only. Filed on
  wq#2883 together with the corpus run; that ticket also converts `install.sh:20`'s
  in-place `install` to the wq#2856 atomic `install-script` helper. Logged as ISSUE-LOG
  row 889 — a merged `ops/` PR does not run that dir's `install.sh`, and nothing checks
  repo-vs-`/home/lumen/bin` drift, so a green ticket can hide an un-deployed fix.
