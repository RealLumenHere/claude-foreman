# wq#3007 verify-pass: wq#2922 [mask] under-floor class — CONTRADICT

**Verdict: CONTRADICT.** wq#2922 is marked `done`, but the class it was filed to close —
a real token whose body holds this module's own `MASKED` literal below the rule's floor
leaking silently (`hits==[]`) — is still open on `main` right now. The fix wq#2922's own
attempt-3 session wrote (`masking._orphan_matches()`, commit `c1301b79` on branch
`feat/wq2922-mask-under-floor-class-still-open-via-th-a2`, worktree `wq2922-a2`) is
reviewed cold below and looks sound in isolation — but it was never merged into `main`.
The bug the ticket title says is "still open" is, measurably, still open.

## Evidence

**1. The ticket's own probe, run unmodified against current `main`:**

```
$ python3 reports/wq2868_probe_underfloor_class.py harness/    # main, harness/masking.py
B under-floor via MASK guard (leak = fail): 96 cases, 96 failing
```

Full breakdown (via a 3-line driver importing `section_b()` directly, since the probe's
own printer truncates to 8 rows): **95 SILENT / 96, 1 alarmed**. The acceptance criterion
wq#2922 wrote for itself was "probe section B 96 cases 0 SILENT" — main is at 95.

**2. `harness/masking.py` on `main` has no orphan-prefix backstop at all:**

```
$ grep -n "orphan" harness/masking.py   # main
(no output)
```

No `_orphan_matches`, no `_ORPHAN_SHAPES`/`_ORPHAN_PATTERNS`. `mask_text()` and `scan()`
are unchanged from before wq#2922 was claimed.

**3. The repo's own tripwire test says the same thing, independently:**

`harness/tests/test_mask_underfloor_invariant.py` carries (docstring, lines 18-21):

> "Section B is the same under-floor class through the OTHER guard `_body()` carries...
> leaks: xfail-strict against wq#2922 so closing that ticket makes this test go red
> until the xfail is dropped."

```
$ python3 -m pytest harness/tests/test_mask_underfloor_invariant.py -q
....x
4 passed, 1 xfailed in 0.40s
```

`test_a_body_holding_the_mask_literal_below_the_floor_still_leaks` is still `xfail`,
not `XPASS`. Under `strict=True` that is deterministic proof the underlying behavior has
not changed — if wq#2922's fix were live on `main`, this run would report a strict-xfail
**failure** (unexpected pass), which is exactly the tripwire the invariant test's author
built it to be. It didn't fire. The fix isn't there.

**4. The docstring wq#2922 was required to correct is untouched:**

`masking.py:113-115` (current `main`) still reads:

> "...it is paid in the direction that alarms rather than the one that goes silent."

wq#2922's own ticket text called this claim "measurably false" and required the docstring
change with the fix. It's still on `main`, unedited, still false.

**5. Where the actual fix lives:** `git log --oneline main --grep="orphan-prefix"` returns
nothing. The commit exists only on an unmerged branch:

```
$ git -C /home/lumen/foreman merge-base --is-ancestor c1301b79 main; echo $?
1   # not an ancestor
```

`c1301b79` ("fix(mask): fail-loud orphan-prefix backstop for the under-floor MASK-literal
class", `Refs: wq#2922`) sits on `feat/wq2922-mask-under-floor-class-still-open-via-th-a2`
(worktree `wq2922-a2`), which was never merged. The sibling branch `-a3` (the attempt the
ticket's `notes` field and `_runs/wq2922/attempt-3.json` cite as the "SUCCESS" run) has
since had its branch ref recycled onto an unrelated later commit (`a9bad1f9`, a wq#2116
vault-gate fix) by a different worker picking up the same worktree slot — there is no
longer any live pointer to whatever attempt-3 actually produced beyond the a2 commit,
which duplicates the same diff (`48f6c6c7` is an identical second copy of the same
`c1301b79` patch, also unmerged). The ticket's `_runs/wq2922/acceptance.json` acceptance
criterion was `proof_type: citation` — "cite file:line of the code that implements it" —
satisfied by pointing at code in the worker's own branch. Nothing in the deterministic
gate required that branch to reach `main`.

## Adversarial review of the fix itself (read cold, diff `c1301b79` vs. its parent)

Extracted `harness/masking.py` + `harness/live_code.py` at `c1301b79` into an isolated
import path and re-ran the probe against it directly (not through `main`, to separate
"is the logic right" from "did it land"):

```
$ python3 reports/wq2868_probe_underfloor_class.py /tmp/.../harness_c1301b79
B under-floor via MASK guard (leak = fail): 96 cases, 96 failing
```

All 96 are `alarmed` (non-`SILENT`) with the 3-line driver — 0 SILENT, matching the
ticket's acceptance criterion for section B when tested in isolation.

`_orphan_matches()` walks the same guarded charset run `_body()` already produces, with
no floor, so it always matches (the design point: a match that can't fail can't go
silent). It fires only when (a) the run stopped exactly on `_OWN_TAIL` (the mask literal,
optionally preceded by a chain of this module's own prefixes) — filtering "short for an
unrelated reason, e.g. a foreign prefix" — and (b) the real characters either side of the
literal, concatenated, would have reached the rule's own floor had the literal not been
sitting between them — filtering the `cfut_MASKED.` prose-resumption case wq#2638 exists
to keep quiet. Tried to break this two ways:

- **Does it re-arm on ordinary prose resuming behind a masked value?** `cfut_MASKED.`
  alone: `before` len 0, `after` (`.` — cloudflare's charset admits it) len 1, combined
  1 < floor 6 → correctly not flagged. The combined-floor test is exactly what stops a
  bare "does the charset continue" check from over-firing here.
- **Does the floor/charset table (`_ORPHAN_SHAPES`) drift from `PATTERNS`?** It's
  hand-duplicated rather than derived, which is a drift risk in principle (the commit
  message names this explicitly as the same concern that killed wq#2848's old
  `_SHAPES_BY_LABEL` table) — but git-blame-adjacent to `PATTERNS` in the same diff, and
  the probe (which reads the six prefixes from its own independent `RULES` table, not
  from `masking.py`) catches a drift as a probe failure, not silently. Did not find a
  live drift as of `c1301b79`.

No break found in the logic itself. **The defect is not in the diff; it's that the diff
never shipped.**

## What this means for wq#2922

wq#2922 should not be treated as closed. The mechanical fix already exists
(`c1301b79` / `48f6c6c7`, identical, on `feat/wq2922-mask-under-floor-class-still-open-via-th-a2`)
and reads sound on inspection — this is a landing gap, not a redo. Escalating per the
verify-pass contradiction protocol rather than re-implementing or landing it directly,
since (a) the fix touches a security-tripwire module and deserves the judgment-tier
review the protocol specifies for a contradiction, and (b) it's worth confirming nothing
downstream (the `token_engine_daemon.mask_hits()` mirror this commit also touches, and
the sed-mirror-is-now-a-thin-wrapper claim) has drifted in the time since `c1301b79` was
cut from what is now a much older `main`.

ISSUE-LOG row 982 filed. Escalation ticket filed per protocol (opus-5@high judgment).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
