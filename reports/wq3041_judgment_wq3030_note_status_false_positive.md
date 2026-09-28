# wq#3041 escalation judgment: wq#3030 note-supersession status-token false positive

- Judge: opus-5@high (escalation judge, adversarial verify-pass)
- Subject: PR #1321, merged as `ecab98f1c4d3d2beed87ab58adf5e974eadbbba1` (wq#3030)
- File: `ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`
- Date: 2026-09-28
- **Verdict: CONFIRMED.** The bug is real and material. It is actually wider than the verifier reported. One part of the verifier's reasoning is wrong (see "Correction to the verifier"), and that changes what the fix should look like.

## The claim

`_note_status_tokens()` treats every token matching `NOTE_STATUS_RE = ^[A-Z][A-Z0-9_-]{1,}$` in a superseded note, which does not reappear in its successor, as a dead status value. Nothing checks whether the token is a status word. So ordinary acronyms (PR, CI, TODO...) become dead values, and any open note that shares the subject slug and mentions that acronym is flagged. The code comment at lines 205-208 justifies the missing guard with "always subject-scoped before it is ever matched". The verifier calls that a non-sequitur.

## Independent verification

All checks ran against the worktree pinned at `ecab98f1` (`/tmp/fworker-wq3041/wq3030-wt`, confirmed with `git log -1`). I used synthetic sqlite only and did not touch minerva.db.

1. **Read the shipped code directly.** The relevant parts:
   - `_note_status_tokens` (l.417-428)
   - `build_note_supersession_index` (l.443-500)
   - `scan_text` (l.535-576)
   - `scan_notes` (l.622-647)
   - `scan_corpus` (l.746-791)
   - `scan` (l.1017-1065)

   The verifier's excerpt is accurate.
2. **Re-ran the verifier's probe** (`/tmp/fworker-wq3041/probe_false_positive.py`). It reproduces: the index gets `('wq1610-widening','PR')`, and note `n3` is flagged.
3. **Wrote an independent probe with controls** (`/tmp/fworker-wq3041/judge_probe.py`):

   | Case | Setup | Result |
   |---|---|---|
   | A | Verifier's scenario, rebuilt from scratch | HIT `wq1610-widening.pr='PR'`, severity `action` (false positive) |
   | B | Control: identical but no acronym in the old note | index empty, no hits. So A is caused by the acronym, not by the probe |
   | C | Intended true positive (OUTAGE -> RESOLVED) | HIT. The module works as the PR intended |
   | D | Old note titled "hathor GPU status update" mentions CI/GPU/TODO; successor doesn't. Unrelated open note "isis status update: CI green on main..." | Index has 9 entries including `status.CI` and `update.GPU`. The **isis** note is flagged for a **hathor** note's `CI` (false positive across fleet subjects) |
   | E | Same `PR` entry, file artifact on the `ticket` tier: "open a PR against main" | HIT `tier=ticket sev=action` |

4. **Shipped test suite:** `pytest -q test_lumen_contradiction_scan.py` gives 80 passed. None of the 5 new tests uses a non-status token, so the suite cannot catch this.

## Why subject-scoping doesn't guard against this (and gaps the verifier missed)

The comment's safety argument depends on the gates in `scan_text`. For note entries, each gate is weaker than it looks:

- **The cue gate always passes.** The predicate is `val.lower()`, and `predicate_cues("pr")` returns `['pr']`, which is compiled case-insensitively. Whenever `value_re` matches `PR`, the cue regex matches the same span. For short tokens the "subject + cue" test is just "subject".
- **The subject can be any ordinary word.** `_note_subject_tokens` takes every title word of 3+ characters, excluding only 5 stopwords (the/and/for/has/was). Two notes titled "... status update" produce subjects `status` and `update`, which match a large share of ops notes (case D).
- **No severity downgrade.** Note entries carry `valid_to=None`, so `dated_before()` is always False and every hit is `severity=action`. Every hit counts toward `strict_actionable` (the timer's red light) and, under `--apply`, is written to `note_contradictions`.
- **The damage goes beyond notes.** `scan()` passes the combined index to `scan_notes`, `scan_files` and `corpus_tier`. On the `ticket` tier, `--apply` runs `file_config_ticket` -> `wq add`. On the `regenerate` tier it triggers the state regen. The production timer runs `--apply --corpus`. The corpus half is report-only there, because there is no `--corpus-apply`, but it still inflates the counts.
- **Only the negation list suppresses.** `current_res` is empty for note entries, so the changelog suppressor never runs. The only filter left is `NEGATION_RE`, which catches incidental words like "was", "old" and "until", not acronyms.

## Correction to the verifier

The verifier describes `SHOUTED_SHORT_RE` as a guard that stops short acronyms from firing. It isn't. At l.760-766 it is a **case-exactness requirement** in `scan_corpus`: short shouted values must also appear shouted in the text. It never drops a value from the index. The note path already matches case-exactly (`boundary_re(val, case_exact=True)`), so it has that guard's full effect. Copying `SHOUTED_SHORT_RE` over would fix nothing, because `PR` in prose is already shouted.

The real flaw is in where the values come from. On the state_facts side, dead values are real status values that someone recorded in a typed column. On the note side they are harvested from free prose with no status vocabulary at all. The comment at l.205-208 is still wrong, because subject-scoping limits where a value is attributed but not whether it is a status. The verifier's conclusion stands; only the analogy is off.

## Materiality

I could not read minerva.db (this lane has no `/home/lumen/minerva` grant, same as the author and the verifier). As a proxy, I ran the exact tokenizer over fleet prose (`/tmp/fworker-wq3041/judge_density.py`), using a generous status list of about 40 words:

- `kb/ISSUE-LOG.md`: 6611 `NOTE_STATUS_RE` matches, of which **5878 (89%) are not status words**. `PR` is the most common token at 667 occurrences. Others: NOT (174), HEAD (156), BUILD (112), CLI (90), API (38), DB (34), PID (30), README (29).
- foreman `git log -500`: 132/138 (96%) are not status words (MCP, ACL, CLI, JSON, UDP...).

Ops notes are written in this same register. For any superseded note that dropped a sentence mentioning an acronym, this path will almost certainly produce a spurious dead value. Such a value fires on any open note that shares a title word and uses the acronym, which is very common for `PR` and `CI`.

**Not verified:** how many live `archival_notes` rows have `supersedes` populated. That number sets the absolute hit count, and if lineage is sparse, the real volume could be small. It does not change the verdict. The precision per entry is poor by construction, the hits are all `action` severity, and they run unattended into writes and ticket filing.

## Verdict

**CONFIRMED.** wq#3030 shipped a precision regression in an unattended writer. The false positive is genuine: it reproduces independently and the control isolates the acronym as the cause. It also extends past the verifier's case, to cross-subject bleed through generic title words and to the ticket-filing tier. It is material given the measured token population. The intended recall gain (case C) is real, so the feature should be tightened, not reverted.

## Recommendation

File a follow-up ticket (build lane, pri 2) to tighten `_note_status_tokens` and the note-entry gates before the timer next runs `--apply` against a corpus with populated `supersedes`:

1. Switch the harvest to an **allowlist of status vocabulary**, for example the status words already in the tests (OUTAGE, CRITICAL, STALLED, RESOLVED, OK, DONE) plus DEGRADED/DOWN/FAILED/BLOCKED/... Do not bolt a denylist of acronyms onto the current regex; there are 1292 distinct shouted tokens in ISSUE-LOG alone, and a denylist will not converge. Keep OK and DONE covered, since the existing tests depend on them.
2. Give note entries a **real cue gate**. The predicate should not be the value itself; use something like a `status`/`state`/`outage` cue set.
3. Require the **subject to be a slug-shaped identifier**, not any title word of 3+ characters. At minimum, exclude generic words like status, update, notes and fleet.
4. Until 1-3 land, **keep note entries out of the `ticket` and `regenerate` file tiers**, so a false positive cannot auto-file a `wq` ticket or trigger a state regen.
5. Add regression tests built from probe cases A, D and E, which must stay silent, alongside the existing true-positive cases.
6. Fix the misleading comment at l.205-208 / l.420-422.

The separate gap of the ticket closing without its acceptance probe (the live wq#1614 trap@1 gate was never run; ISSUE-LOG #993, disclosed) is outside this judgment. The follow-up should re-run that gate once a lane with a minerva grant picks it up, since tightening item 1 will change trap@1.
