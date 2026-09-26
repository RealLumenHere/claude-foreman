# wq#2330 — verify of wq#2291 (no PR — NO-ARTIFACT rotation ticket, closed 2026-09-20)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2291 claims the wq#2275 mask tripwire that fired on wq#2278 attempt 1's
stream-json (label `github`) was a **false positive** — the tripwire fired on
`token-engine/gate_replay.sh:54`, the broker's own self-test probe string
`ghp_selftest`, not a real credential — and that **nothing needs rotating**,
because the leak path (unanchored `github` regex) was already closed on main
by e0a4d06 (wq#2292) + 32aa000 (wq#2288). It landed no diff (NO-ARTIFACT).
I did not trust the write-up at face value; I reproduced the trigger and the
fix independently from raw event logs and frozen file snapshots.

## Per claim

| wq#2291 claim | Result | Evidence |
|---|---|---|
| Trigger pinned via `session_id=c2828873-…` from `events/fworker-wq2278-a1.jsonl`'s start record | CONFIRMED | Read the start record directly: `session_id` field is exactly `c2828873-c142-446d-81e8-f3740856ba35`. |
| Of the recorded reads, exactly one trips the pre-fix github rule — read #9, `token-engine/gate_replay.sh:54` | CONFIRMED, with one caveat | `events/read_cache/c2828873-….jsonl` has 26 entries, not the 20 the notes state — but entry #9 is exactly `token-engine/gate_replay.sh` (entry #8 is `install.sh`, matching the cited `grep -c ^ install.sh gate_replay.sh`). Line 54 is `echo "$OUT" \| grep -q 'ghp_selftest' && …`. Scored the raw pre-fix regex `(ghp_\|gho_\|ghu_\|ghs_\|ghr_\|github_pat_)[A-Za-z0-9_]{6,}` against the **byte-exact content each file had at read time** (matched by the read cache's own `fp` size field to a same-size pre-edit snapshot in `_runs/wq2278/snapshots/`, not the current worktree — the worktree's copies of `token_engine_daemon.py`/`test_daemon.py` have since grown from 10716/4994 bytes to 52757+ bytes and now contain unrelated `ghs_`/`highs_solver` doc comments that would give false hits if scored as-is). Result: zero hits on `token_engine_daemon.py` (read #3) and `test_daemon.py` (read #5) at their as-read sizes; exactly one hit (`ghp_`) on `gate_replay.sh` (read #9, unchanged since — current file is still 2681 bytes, matching the fingerprint exactly). The 20-vs-26 count is a minor inaccuracy in the write-up; the indexed claim it rests on is exact. |
| The anchored fix (`{30,}` / `{20,}` floors, word-boundary anchor) no longer fires on `ghp_selftest` | CONFIRMED | Ran both current `harness/masking.py` github patterns directly against `gate_replay.sh:54`'s literal text: no match (`ghp_selftest` body is 8 chars, needs 30+). |
| Leak path closed on main by e0a4d06 (wq#2292) + 32aa000 (wq#2288) | CONFIRMED | Both commits exist in `origin/main` history, unreverted (`e0a4d06` 2026-09-20 22:35:49, `32aa000` 2026-09-20 22:40:21). `harness/masking.py`'s live github rules match e0a4d06's anchored patterns verbatim. |
| No live credential reachable (github remote carries no embedded userinfo) | CONFIRMED, independently reproduced | `grep -c '://[^/]*@' .git/config` on the verify repo → `0`. |
| No mask-hit evidence file exists for wq2278 attempt 1, because `write_mask_hits()` (32aa000) postdates the run | CONFIRMED | `_runs/wq2278/` has no `mask-hits-*.json`; consistent with 32aa000 landing 24 min after wq2278's ticket-filing event. |

## What I tried to break

- Re-scored the pre-fix regex against the **current** worktree files at the
  three paths involved (`token_engine_daemon.py`, `test_daemon.py`,
  `gate_replay.sh`) before checking history — got **3 hits**, not 1, which
  looked like a direct contradiction of "exactly one". Traced it: the daemon
  file was expanded later in the same wq2278 attempt (and further since) to
  add the widened self-test canary and doc comments describing the
  `ghs_`/`highs_solver` false-positive class itself — content that did not
  exist at actual read time. Re-scored against fingerprint-size-matched
  pre-edit snapshots instead and the false hits disappeared. This is the same
  trap the tripwire itself falls into (scoring live/current text instead of
  the frozen moment) — good that wq#2291's own reconstruction avoided it.
- Checked whether reads made *after* the ticket was already filed (indices
  10–26, in the same attempt) also trip the old rule — two do (`ghs_` inside
  `highs_solver`-explaining comments in the daemon file, sizes 52757/52388),
  but that's the well-documented ISSUE-LOG rows 568/570/573 self-referential
  class, not a second live credential, and it postdates the ticket this
  verify covers.
- Ran `harness/tests/test_masking.py` (64 tests) as the probe: 63 pass, 1
  fails — `test_doctrine_corpus_does_not_re_arm_the_alarm`. Traced this to a
  **different, already-tracked** bug: ISSUE-LOG row 724 (wq#2350, verify-pass
  of wq#2298, landed today) already contradicts a claimed fixed-point property
  for the `cloudflare`/`kv-secret` rules specifically — quoted `TOKEN="MASKED"`
  / `cfut_MASKED` examples in ISSUE-LOG's own prose re-arm those two labels
  because their body charset swallows trailing punctuation. That's orthogonal
  to wq#2291's `github`-label claim (which is what this ticket verifies) and
  is already escalated to opus-5@high judgment via wq#2350/row 724 — not
  re-logged or re-fixed here.

## Conclusion

Every load-bearing claim in wq#2291 reproduces independently from raw event
logs and byte-exact historical file state, not from trusting the author's
narrative: the exact trigger read, the pre-fix rule's single hit at that
moment, the anchored fix's absence of a hit today, the fix commits' presence
and un-reverted state on main, and the absence of any live credential in the
repo's own remote config. No credential was burned. CONFIRM.
