# wq#2672 — verify of wq#2669 (PR#1035, merged a555e15, deps=2620)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2669 was the judgment ticket on wq#2644's CONTRADICTED verify-pass of PR#1008
(`43d7e96f25`, wq#2620's original safe worktree pruner): `_TS_FMT` was ISO-8601
(`%Y-%m-%dT%H:%M:%SZ`) while the real wq db stores `%Y-%m-%d %H:%M:%S`
(`bin/wq:145`, `bin/slock:43`), so `_parse_ts()` failed on every real value and
both `wq_claim_is_live()`/`slock_is_live()` treated the `None` result as "no
evidence of expiry" — an expired lease or slock was reported live forever, and
the test fixture (`2000-01-01T00:00:00Z`, ISO) matched the buggy parser and hid
it. wq#2669's ruling: split `_TS_FMT` into `_DB_TS_FMT` (db reads) and
`_LOG_TS_FMT` (own JSONL log, `append_log:309` was a hidden third consumer that
a naive single-constant fix would have silently reformatted mid-file), make
`_parse_ts` strict rather than tolerant of both shapes, make an unparseable
value say so in the skip reason instead of reading as an ordinary live claim,
and correct + extend the test fixtures to the real format rather than importing
the constant under test.

Read the diff cold (`git show a555e15`: `prune_worktrees.py` +21/-9,
`tests/test_prune_worktrees.py` +90/-9 net five new tests, new
`probe_wq2669_writer_format.py` +82, `README.md` +16, new judgment doc +126).
Did not re-implement.

## Per claim

| wq#2669 claim | Result | Evidence |
|---|---|---|
| pre-fix `_TS_FMT` fed both db parses (`:208`,`:245`), only two `_parse_ts` callers | CONFIRMED | `grep -n "_parse_ts\|_TS_FMT" ` on `a555e15~1:prune_worktrees.py` — exactly two call sites, one constant, one hidden third consumer (`append_log`) |
| real db format is `%Y-%m-%d %H:%M:%S`, both writers agree | CONFIRMED | live read: `bin/wq:145` and `bin/slock:43` both `FMT = "%Y-%m-%d %H:%M:%S"`; matches `harness/liveness_clear.py:453`'s independent `_WQ_TS_FMT` |
| fix splits `_DB_TS_FMT`/`_LOG_TS_FMT`, both `_parse_ts` callers now use the db format, `append_log` keeps the log format | CONFIRMED | `grep -n "_TS_FMT\|_parse_ts" prune_worktrees.py` post-fix: no stray reference to the old name anywhere in the repo (`grep -rn "_TS_FMT\b" foreman` finds only the two new, correctly-scoped constants + comments) |
| pinned probe (`bin/wq`+`bin/slock` FMT @`6c1c1be`) passes fixed, fails pre-fix | CONFIRMED, reproduced independently | ran `probe_wq2669_writer_format.py` against HEAD: `PASS: both expired records read as expired`, exit 0. Copied pre-fix `prune_worktrees.py` (`a555e15~1`) into an isolated dir with the same probe: `FAIL: expired lease reported live; expired slock reported live`, exit 1 |
| suite 13→17 tests, `4 failed/13 passed` when post-fix tests run against pre-fix code | CONFIRMED, reproduced independently | `pytest tests -q` on HEAD: `17 passed`. Copied post-fix `tests/test_prune_worktrees.py` onto pre-fix `prune_worktrees.py`: `4 failed, 13 passed`, same four names (`test_expired_lease_does_not_block_pruning`, `test_parse_ts_reads_the_format_wq_actually_writes`, `test_unparseable_lease_fails_closed_with_a_reason_that_says_so`, `test_expired_slock_does_not_block_scratch_worktree`) |
| strict parser (rejects ISO, doesn't accept both shapes) | CONFIRMED | `pw._parse_ts("2000-01-01T00:00:00Z")` → `None`, asserted in `test_parse_ts_reads_the_format_wq_actually_writes` and reproduced |
| unparseable now fails closed *and says so* | CONFIRMED | code path returns `"...unparseable — fail closed"` explicitly before the comparison; test seeds `lease_expires='whenever'`, asserts `action=="skip"` and `"unparseable" in reason` — passes |
| no second-order effect on other `_TS_FMT`/`_parse_ts` consumers outside this file | CONFIRMED | `grep -rn "_TS_FMT\|prune_worktrees" foreman/ bin/ kb/` — only `install.sh` (invocation wrapper, doesn't parse output/log), no other reader of the JSONL log or the reason strings |
| fleet dry-run unaffected, 0 expired leases/slocks live | CONFIRMED (state drifted, logic unaffected) | ran `prune_worktrees.py --json` against the live db: 0 `unparseable`/`lease expired`/slock-expiry hits either way. Scanned/prunable counts differ from the PR's own numbers (1482/32 here vs 1456/25 there) — expected, ~2h of fleet churn between runs, not a functional discrepancy |
| ISSUE-LOG #767 citation | CONFIRMED | row 767 is real and matches (the `~/.claude/projects` transcript-guard snag hit during this ticket's own work, not the substance of the fix) |

## Attempted contradiction

Looked for: a third `_parse_ts`/`_TS_FMT` consumer missed by the split (none —
verified exhaustively above); a fixture that imports the constant under test
and would drift with it (checked — `tests/` spells `WQ_TS_FMT` as a local
literal, exactly as the judgment doc requires); a reason-string consumer that
the wording change could break (none found); timezone mismatch between
`_now_utc_naive()` and the stored values (both naive-UTC by construction,
`bin/wq`'s `now()` also strips tzinfo via `strftime`). Ran the negative control
myself rather than trusting the PR's transcript, on both the probe and the full
suite, and got identical results. No contradiction found.

## Disposition

wq#2669's fix and judgment stand. CONFIRM — no escalation to opus-5@high.
