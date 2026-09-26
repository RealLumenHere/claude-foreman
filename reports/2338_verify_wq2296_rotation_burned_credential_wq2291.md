# wq#2338 — verify of wq#2296 (claude-foreman PR#817, merged squash 28ae16581b)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2296 claims the wq#2275 mask tripwire that fired on wq#2291 attempt 1's
stream-json (label `github`, 22:20:25) was a **false positive** — same
`highs_solver`/`ghp_MASKED` class as ISSUE-LOG rows 568/570 — and that
**nothing needs rotating**. It lands only one change: a new regression test
(`test_doctrine_corpus_does_not_re_arm_the_alarm`, since folded into
`test_the_mask_suite_does_not_arm_its_own_tripwire`) that scans the live
`kb/ISSUE-LOG.md` and the mask suite's own source through `masking.scan()`.
I did not trust the author's write-up at face value; I reproduced every load-
bearing claim independently.

## Per claim

| wq#2296 claim | Result | Evidence |
|---|---|---|
| The rule live at the 22:20:25 alarm was the unanchored, pre-fix `(ghp_\|gho_\|ghu_\|ghs_\|ghr_\|github_pat_)[A-Za-z0-9_]{6,}` | CONFIRMED | `grep -n` on the frozen snapshot `_runs/wq2291/snapshots/20260920T222917Z-home_lumen_foreman_harness_masking.py:26` shows exactly that unanchored pattern. |
| The anchored fix landed AFTER the ticket was filed, so the alarm never ran against it | CONFIRMED | `git show -s --format=%ci e0a4d06` → `2026-09-20 22:35:49 +0000`. Ticket detail states the tripwire fired at 22:20:25 — fix lands 15 min later. Ordering matches exactly. |
| `kb/ISSUE-LOG.md` scores 0 credential records under the anchored rule | CONFIRMED, independently reproduced | Ran `masking.scan()` (current `harness/masking.py`, imported fresh, not the author's numbers) against the live corpus: `records == []`. |
| The old unanchored rule would have caught these same strings (falsifiability / the test isn't vacuous) | CONFIRMED, and strengthened | Ran the raw pre-fix regex against the live corpus directly: 37 matches today (all `ghs_solver`/`ghp_MASKED`/`ghp_selftest` shapes — the false-positive class), vs. the 8 the author counted on 2026-09-20. The corpus has grown by ~5 days of further false-positive documentation since, and the anchored rule still returns 0 on all of it — the test's regression value has only gotten stronger, not weaker. |
| No mask-hit evidence record exists for wq#2291 because `write_mask_hits()` (32aa000) landed after this run | CONFIRMED | `git show -s --format=%ci 32aa000` → `2026-09-20 22:40:21 +0000`, after both the wq#2291 run start (22:19:44) and the ticket filing (22:20:25). `events/fworker-wq2291-a1.jsonl` holds only `gate_amber_snapshot`/`start`/`blocked` events, no mask-hit record — matches. |
| Probe passes; no regressions | CONFIRMED | `pytest harness/tests/test_masking.py -q`: 64 passed (suite has grown since the author's "17... 53 across adjacent" count — expected, this repo has had 5 days of further commits). Ran the probe test alone (`-k "doctrine or suite_does_not_arm"`): 2 passed, not skipped (`kb/ISSUE-LOG.md` exists on this host, so `skipif` does not fire). |
| NEW FINDING (wq#2333, filed separately): deployed `/usr/local/lib/token-engine/token_engine_daemon.py` is a stale pre-anchor copy that DENIES PR bodies quoting these shapes | NOT THIS TICKET'S SCOPE | Correctly filed as its own pri-1 ticket rather than folded into this rotation ticket's diff — deploy drift is a distinct root cause from "is there a live credential," and fixing it here would have been unscoped work. Left as-is; verified it's tracked, not silently dropped. |

## What I tried to break

- Checked whether the snapshot's mtime/filename-timestamp mismatch (mtime 22:09
  vs. filename `20260920T222917Z`) was a discrepancy the author missed — it
  isn't: the filename is snapshot-capture time, the mtime is masking.py's own
  last-modified time, and the frozen content is what matters. Consistent with
  the author's claim.
- Looked for a pinned single trigger read (the way wq#2291's own resolution of
  wq#2278 named `gate_replay.sh:54` exactly) — none exists for wq#2291 itself,
  because the evidence-recording feature (32aa000) didn't exist yet. That's a
  real gap in what's provable, but the author already surfaces it explicitly
  (as the NEW FINDING) rather than hiding it, and the timing + full-corpus-scan
  argument doesn't depend on knowing the single trigger.
- Re-ran the falsifiability check myself with the raw pre-fix regex rather than
  trusting the author's "8 records" figure — got 37 today, same false-positive
  class, zero live credentials. No shape slipped through.

## Conclusion

Every claim in wq#2296's done-notes reproduces independently: the timing
ordering, the frozen unanchored rule at alarm time, the live corpus scanning
to zero under the current anchored rule, the falsifiability of the new test,
and the absence of any evidence record (because the recorder didn't exist yet
at run time). The diff is minimal and correctly scoped — it lands only the
regression test, defers the unowned pattern-fix and the stale-deploy-copy
finding to already-filed follow-up tickets. No credential was burned. CONFIRM.
