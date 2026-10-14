# wq#3832 — verify-pass of wq#3831 [rotation] burned credential sha256:e4e6de63c12d (jwt)

**Verdict: CONFIRMED.** wq#3831's judgment call ("committed test fixture, no rotation") holds
up under independent recomputation. No PR was shipped by wq#3831 — it closed the rotation
ticket on replay evidence alone, which is the correct outcome for this evidence. One process
gap found and fixed during this verify-pass (missing ISSUE-LOG row — see below).

## What wq#3831 claimed

- `mask_replay.py 3830` exits 0: 6 credential-shaped values in wq#3830 attempt 1's model
  context, 0 unaccounted.
- The burned value (jwt, len 29, sha256:e4e6de63c12d) is the committed fixture at
  `harness/tests/test_masking.py:69`, echoed when wq#3830 read the masking self-test output
  (the `OK jwt ...` line).
- The one `kv-secret` `PUBLIC_KEY=` candidate was suppressed by the live ruleset and is
  audit-only, not part of the rotation decision.
- Also cleared wq#3830's stale `lumen_busy` key (pid 2231625, dead).

## What I checked, independently of the tool's own narration

1. **Re-ran the probe.** `python3 -I harness/mask_replay.py 3830` from the `foreman`
   checkout (branch `main`, up to date with `origin/main`) reproduces the exact claim: "6
   credential-shaped values in the run's model context, 0 unaccounted," with the
   jwt/`e4e6de63c12d` row attributed to `harness/tests/test_masking.py:69`.
2. **Did not trust the tool's own match.** Independently recomputed sha256 over the fixture's
   own committed source, outside `mask_replay.py` entirely. Line 69 is
   `JWT = "eyJ" + "A" * 8 + "." + "B" * 8 + "." + "C" * 8`. Built that exact string with
   plain `hashlib` (`python3 -I`, no repo code imported): length 29, `sha256(...)[:12]`
   = `e4e6de63c12d` — an exact match, computed with no dependency on `mask_replay.py`'s own
   matching logic.
3. **Checked for retroactive laundering.** `git log --follow -p` on `test_masking.py` shows
   the JWT fixture line was added in commit `5a56128c` ("fix(mask): add five token families,
   wrap mask-secrets"), committed at `2026-10-14T01:50:50Z`. wq#3830's replayed run started
   at `2026-10-14T01:51:30Z` — the fixture predates the run by 40 seconds, consistent with
   wq#3830 being the very next ticket dispatched after the commit landed, not a fixture
   planted after the fact to launder a real leak. The commit is on `main`, matches
   `origin/main` HEAD — not a local-only or unpushed change.
4. **Verified the accounting method is non-gameable.** `mask_replay.py`'s `committed_text()`
   (harness/mask_replay.py:551-559) builds its "tracked" corpus from `git ls-tree -r -z
   --long HEAD` against the repo's actual HEAD tree — it cannot be satisfied by anything that
   isn't genuinely committed and reachable from HEAD.
5. **Cross-checked the raw hit record.** `_runs/wq3830/mask-hits-1.json` records
   `label=jwt, prefix='', value_len=29, value_sha256_12=e4e6de63c12d`, with a `context`
   string that already reads `...jwt   in='MASKED' got='MASKED' want='MASKED'` — i.e. the
   on-disk artifact itself is masked; only the model's transcript (not disk) carried the
   value, matching wq#3831's (and instinct 4's) framing exactly.
6. **Checked for other burn sites.** Per wq#2341, this row is keyed by value, and any other
   run that printed the same value should be appended to wq#3831's notes as `also fired on
   wq#N`. Grepped `_runs/` fleet-wide for `e4e6de63c12d`: only `wq3830/mask-hits-1.json` and
   wq#3831's own evidence/episode files reference it. No other run is implicated, so no
   `also fired on` entries were missing.
7. **Checked ISSUE-LOG bookkeeping — found a gap.** The immediately preceding sibling ticket
   in this same series, wq#3828 (verified by wq#3829), logged its own occurrence as
   ISSUE-LOG row 1290 even though it was a recurrence of an already-known pattern (row 800).
   wq#3831 did the equivalent investigation and reached the correct conclusion, but its
   ticket notes never ran `issue_log_append.py` — grepping `kb/ISSUE-LOG.md` for `wq3830` or
   `wq3831` before this verify-pass returned nothing. Doctrine requires recording every issue
   hit, not just the ones that need code changes. **Fixed during this verify-pass**: appended
   ISSUE-LOG row 1292 documenting wq#3831's occurrence (symptom/cause/solution mirroring row
   1290's shape), via `issue_log_append.py` under its lock, not a hand-picked row number.

## What would have broken this verdict (and didn't)

- If the fixture string's hash had **not** matched `e4e6de63c12d` under independent
  `hashlib` computation → would mean `mask_replay.py` mis-attributed the line and the real
  value might still be live. It matched exactly.
- If the fixture commit postdated the wq#3830 run → would mean someone planted the fixture
  to launder a real leak after the fact. It predates the run (by 40 seconds, the normal gap
  between two sequentially dispatched tickets), and is merged to `origin/main`.
- If another run anywhere in `_runs/` had printed the same value with a different leak path
  → would mean wq#3831 closed the row without covering every burn site (wq#2341). None found.
- If `mask_replay.py`'s "tracked" corpus were built from something other than `git ls-tree
  ... HEAD` → would mean the 0-unaccounted result could be gamed by any string that merely
  resembles repo text. It is built from HEAD's actual tree.

## Scope note

wq#3831 shipped no diff/PR — it is a pure judgment ticket (run a tool, read its output,
close with "no rotation"). There is nothing to review for code-quality or regressions; the
verify surface here is "is the stated evidence true, and was the required bookkeeping done,"
both checked above by recomputing independently rather than re-trusting the same tool's
output. The technical rotation verdict is fully confirmed. The one gap found (missing
ISSUE-LOG row) was a bookkeeping omission, not a wrong conclusion, and has been corrected
in-place (row 1292) rather than treated as a contradiction of the rotation judgment.

No escalation to judgment needed — board this as verified.
