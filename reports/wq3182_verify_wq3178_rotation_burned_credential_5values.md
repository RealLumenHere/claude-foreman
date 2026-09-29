# wq#3182 — verify wq#3178 (rotation, burned credential 5 values set:c993c0980272)

**Verdict: CONFIRMED.** wq#3178's settlement holds: all 5 credential-shaped values that
fired the wq#2275 mask tripwire on wq#3167's stream-json are accounted for as committed
test fixtures (or no longer matched by current masking rules for reasons independently
verified), not a real credential. No rotation needed, and no PR/diff exists to review —
this was a pure investigation ticket with zero code change, which is the correct shape
for its finding.

## What wq#3178 shipped

No code, no PR (notes: "No code change, no PR ... [UNLANDED: evidence.json is the
fworker per-run replay artefact; ticket settled with zero code change so there is no
diff to land]"). The deliverable is `/home/lumen/foreman/_runs/wq3178/evidence.json` +
`evidence/attempt-1/replay-AC1.log`, the captured output of
`python3 /home/lumen/foreman/harness/mask_replay.py 3167`, claiming exit 0, 5
credential-shaped values, 0 unaccounted.

## Verification performed

1. **Reproduced the probe verbatim.** Re-ran `python3 /home/lumen/foreman/harness/mask_replay.py 3167`
   fresh. Exit code 0. Same 5 records, same 5 sha256-12 values, same "0 unaccounted"
   verdict. Only cosmetic diffs vs. the archived log: header-line ordering (log-capture
   wrapper artifact) and one `accounted_in` line number shifted by +1
   (`test_read_dir_sweep.py:255` → `:256`) because that file gained one line between the
   two runs — expected, since `account()` re-scans the live tracked tree rather than a
   pinned snapshot, not evidence of drift in the underlying claim.

2. **Reconstructed the ticket's title hash from ground truth**, same method as
   wq#3136's verify (`fworker.py:1139`: `sha256("\n".join(sorted(hashes)))[:12]` over
   `wq3167/mask-hits-1.json`'s unsuppressed entries). All 5 `mask-hits-1.json` rows are
   unsuppressed; `sha256` of their sorted `value_sha256_12` list ==
   `c993c0980272` — an exact match to the ticket title. This ties the row to the actual
   alarm, and confirms mask_replay's 5 records are precisely the 5 that fired (no extra,
   none missing).

3. **Independently verified each of the 3 "tracked" records** against the tracked tree
   named in `accounted_in`, not just trusting the tool's own report:
   - `http://admin:<14 chars>` → `harness/tests/test_masking.py:509`,
     `WQ2677_PW = "hunter2hunter2hunter2"`. The 14-char value is exactly the leading
     substring of this 21-char literal (both are `"hunter2hunter2"` prefixed) — a real
     substring match, not a coincidence.
   - `postgres://app:<15 chars>` → `harness/tests/test_read_dir_sweep.py:256`
     (`_url("app", "p" + "4ssw0rd-long-1", scheme="postgres", ...)`) — `"p4ssw0rd-long-1"`
     is exactly 15 chars, defined in-file.
   - `HTTPS://x-access-token:<15 chars>` → `ops/wq1298-gitleaks-detect/test_gitleaks_detect.py:55`
     (`secret="g" + "hp_realtoken1234567890"` → `"ghp_realtoken1234567890"`). The 15-char
     value `"token1234567890"` is a genuine contiguous substring of that literal
     (`...rea|token1234567890`), confirmed by direct inspection, not merely the tool's
     say-so.

4. **Adversarially chased the 2 "rule-fixed" records**, since `accounted_in: None` on
   those two looked, at first pass, like the weaker half of the claim (excused by
   "current rules don't match" rather than by being found in tracked text). Recomputed
   `sha256("Kf3" + "x9Zq2Lm")[:12]` locally (value never printed) and it equals
   `e797736247a1`, the recorded hash for the `https://lumen:<10 chars>` record. That
   string is exactly `harness/tests/test_read_dir_sweep.py:254`'s
   `_url("lumen", "Kf3" + "x9Zq2Lm")` fixture. It reads as `rule-fixed` rather than
   `tracked` only because `_url()` builds the full URL via runtime concatenation of
   *variables* (`scheme + "://" + user + ":" + pw + "@" + host`), which
   `mask_replay.account()`'s seam-joiner (built for adjacent `"a" + "b"` literal
   concatenation only) cannot statically reconstruct — a tool limitation, not a false
   "no rotation" call, since the value is provably the same dummy fixture either way.
   The 5th record (`x-access-token:` kv-secret, len 39) is the same line's kv-secret
   rule capturing a wider span (`"token1234567890@100.64.0.7/git/repo.git"`, 15+24=39
   chars) than url-auth's boundary-stopped 15 — same fixture, different rule capture
   width, same benign conclusion.

5. **Checked for "also-fired" runs**, since wq#2341's dedup-by-value rule requires
   logging any *later* run that reprints one of these hashes instead of opening a new
   pri-1 row. `grep -rl` for the 5 hashes across every `_runs/*/mask-hits-*.json` found 4
   additional hits — `wq2316`, `wq2913`, `wq2924`, `wq2120` — but all 4 predate wq#3167
   (2026-09-21 through 2026-09-28, vs. wq#3167 at 2026-09-29T22:18:32Z). None are later
   runs, so wq#3178's "No also-fired runs" is correct under the rule's own (forward-looking)
   scope — it is not asserting these values never appeared before wq#3167, which would be
   false, only that nothing *since* has reprinted them uninvestigated.

## Adversarial angles tried and ruled out

- Suspected the `rule-fixed` mark might be masking a real gap (tool says "not found in
  tracked tree" for 2 of 5 records) — ruled out by hash-matching both values directly to
  named test-file literals, confirming they are the same dummy fixtures the `tracked`
  records are, just unreachable by the tool's static seam-join.
- Suspected the 4 pre-existing hash collisions in older runs meant wq#3178 undercounted
  "also fired" — ruled out on timestamps: all 4 are historical, predating the alarm this
  row is keyed to, outside the dedup rule's forward-looking scope.
- Suspected `test_masking.py:509`'s match on the `admin:` password (len 14 vs. the
  21-char `WQ2677_PW` literal there) could be `_find`'s truncation fallback matching a
  short common substring by luck — ruled out: the 14-char value is the literal's own
  leading 14 characters (`"hunter2hunter2"` vs `"hunter2hunter2hunter2"`), and is also
  independently defined verbatim at `test_read_dir_sweep.py:255`, so either location
  alone would settle it.
- Considered whether the `postgres://app:` line-number drift (255→256) was evidence the
  archived replay ran against stale/different tree state than claimed — ruled out: the
  file gained exactly one line between the two runs (unrelated edit), the fixture and its
  length are otherwise identical, and my fresh run still reports 0 unaccounted end to
  end.

## Conclusion

Every value in wq#3178's 0-unaccounted claim traces to a real, inspectable dummy
credential in the tracked test suite (`test_read_dir_sweep.py`'s deliberately
seam-split `_url()`/kv-secret fixtures used to exercise the masking detectors
themselves, plus one pre-existing fixture in `test_gitleaks_detect.py`). The title hash
reconstructs exactly from raw ground truth, tying the settled row to the real alarm. No
rotation was needed and none was performed; the "no code change, no PR" shape is correct
for a pure RCA-investigation ticket. Confirming on the board, no escalation.
