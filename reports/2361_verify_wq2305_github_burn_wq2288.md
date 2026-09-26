# wq#2361 — verify of wq#2305 (RCA, no PR — `foreman/_runs/wq2305`, done 2026-09-20 23:15:23Z)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2305 investigated a `github`-label mask alarm auto-filed on wq#2288's own
transcript (wq#2288 was itself the rotation ticket for an earlier `kv-secret`
alarm on wq#2284 — the self-feeding chain documented at ISSUE-LOG rows 569/577/578/580).
There is no PR: wq#2305's conclusion was that no code fix was needed beyond what
wq#2288/wq#2292/wq#2300/wq#2303/wq#2319 already shipped or had in flight, so I
independently reproduced its forensic claims rather than reviewing a diff.

## Per claim

| wq#2305 claim | Result | Evidence |
|---|---|---|
| github label fired on 3 values, all hash-matched to committed text, none a live credential | CONFIRMED | Recomputed all three independently. `masking.py`'s github pattern splits `(prefix)(body)` and hashes only the body (`harness/masking.py:286,290,302`: `m.group(0)[len(m.group(1)):]`). `sha256("MASKED")[:12]=20227a136ec3` ✓, `sha256("solver")[:12]=b8789db0c2da` ✓, `sha256("selftest")[:12]=d354caf95d3e` ✓ — all three reproduce the exact hashes cited in wq#2305's notes from scratch, no lookup used. |
| MASKED @ `aa52797:harness/tests/test_masking.py:29,83` | CONFIRMED | `git show aa52797:harness/tests/test_masking.py` lines 29 and 83 both contain the literal `ghp_MASKED` (the masker's own replacement marker), predating wq#2288's run (aa52797 landed 22:09:47Z, wq#2288 started 22:19:34Z per `events/fworker-wq2288-a1.jsonl`). wq#2288's own snapshots show it editing this exact file (pre-images at 22:28:00Z-22:29:00Z), pulling the pre-existing marker into context. |
| solver @ `kb/ISSUE-LOG.md:628-666` (`ghs_` inside `highs_solver`) | CONFIRMED | Rows 548 (line 628) and 554 (line 637) both quote `highs_solver` verbatim, both pre-existing (written 2026-09-19/20, well before wq#2288). wq#2288's snapshots show it reading/editing `kb/ISSUE-LOG.md` (pre-images 22:36:36-22:40:17Z) while researching prior art for its own kv-secret investigation, which is how this pre-existing text entered its context. |
| selftest @ `kb/ISSUE-LOG.md:660` (`ghp_selftest`) | CONFIRMED, with a refinement | Line 660 (inside row 569, wq#2288's own written entry) contains the literal `ghp_selftest`, referring to `token-engine/gate_replay.sh:54`'s self-test probe string — not a live credential. Refinement: this string is *not* pre-existing prior art like `highs_solver` — it does not appear in any of the four `ISSUE-LOG.md` pre-image snapshots wq#2288 itself generated (22:36:36/22:36:59/22:37:02/22:40:17Z), so it was added by wq#2288's own final edit as it documented this very investigation (the same self-feeding amplifier as ISSUE-LOG row 580: the fix's own documentation re-arms the tripwire on its own patch text). It is now committed (verified at current HEAD), so wq#2305's "committed text" characterization holds today regardless. |
| wq#2288 ran under aa52797's unanchored rule for its whole run, despite e0a4d06 anchoring mid-run | CONFIRMED (mechanism), timing phrase imprecise | `e0a4d06` (anchor fix) landed 22:35:49Z, inside wq#2288's 22:19:34-22:40:52Z run window, not before it — so "8 min before e0a4d06 anchored it" doesn't match either endpoint literally (gap from run start is ~16m, from run end is ~5m). The underlying claim is still correct on the real mechanism, documented separately in `masking.py`'s `deployed()` docstring: an fworker process scores its entire stream with the rule table it imported at spawn (22:19:34Z, before 22:35:49Z) and never reloads mid-run — so the whole run used the stale unanchored rule regardless of wall-clock time during it. Cosmetic imprecision in the notes' one-line summary, not a wrong conclusion. |
| Closure VERIFIED: `masking.scan()` at HEAD returns 0 records on all era corpora; 36/36 tests green | CONFIRMED, and still holds 5 days later | Re-ran `masking.mask_text()` at current HEAD against all three literal strings (`"ghp_MASKED"`, the `highs_solver` sentence, the `ghp_selftest` sentence) — zero hits, all three. Ran `harness/tests/test_masking.py` + `test_burn_filter.py` at current HEAD: 102 passed (grown from the 36 wq#2305 saw, via later hardening — no regression). |
| `_runs/wq2288` has no mask-hits file because `write_mask_hits()` was wq#2288's own deliverable | CONFIRMED | wq#2288's own notes (PR #815, merged 32aa000): "`masking.scan()` now returns a redacted record ... `fworker.write_mask_hits()` persists it to `_runs/wq<id>/mask-hits-<n>.json`" — the persistence mechanism did not exist yet when wq#2288's own run happened, so it could not have written its own artefact. Confirmed no such file exists in `_runs/wq2288/`. |
| Mask-suite self-tripwire duplicate found in flight under wq#2303, landed as 74dcb82 mid-session, so wq#2305's own duplicate fix was reverted rather than shipped | CONFIRMED | `74dcb82` exists: `test(mask): stop the mask suite arming its own tripwire`, timestamped 23:12:14Z — 3 minutes before wq#2305's own `done_at` (23:15:23Z), consistent with "landed mid-session." |
| Residual filed as wq#2359 (22 open pri-1 rotation + 12 verify, all unclaimed, ~$10/run) | CONFIRMED | wq#2359 exists with matching detail text, and has since gone `done` (PR #859, merged 512b581741): batch-triaged 47 tickets total (43 clean, 4 to rotate, 0 credentials burned) using the same hash-match method wq#2305 used here — independent confirmation of the whole false-positive class, not just this one ticket. |

## Adversarial checks that did not break it

- Scanned every edit-pre-image snapshot wq#2288 itself generated (38 files) with the
  exact unanchored github regex: found only 2 of the 3 claimed values directly in that
  narrower corpus (`solver`, `MASKED`) — `selftest` is absent because it was added by
  a final edit with no captured pre-image (explained above), not because the claim is
  wrong; the string is verified present in the current committed tree by direct search.
- Checked whether wq#2305's verdict depended on wq#2319's burn-filter kb-provenance fix
  (`burn_filter.provenance_roots`, PR #835) landing first — it doesn't: wq#2319 was
  claimed 23:29:19Z / done 23:44:31Z, *after* wq#2305 closed (23:15:23Z). wq#2305 used
  direct git-forensic hash-matching, not `burn_filter.classify()`, so the ordering
  doesn't undermine it.

## Conclusion

All three named github-label hits in wq#2288's transcript independently reproduce to
non-credential, currently-committed text — two pre-existing (test literal, prior-art
doc reference), one generated by wq#2288's own documentation of this very investigation.
Closure holds at current HEAD (0 mask hits on all three strings, 102/102 tests green,
no regression in 5 days). The one wording imprecision (an approximate "8 min" framing)
doesn't affect the substance — the real mechanism (process-spawn rule staleness) is
independently confirmed. CONFIRM.
