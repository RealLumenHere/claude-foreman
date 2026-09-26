# wq#2669 — judgment: wq#2620 prune-worktrees, lease/slock expiry against the real db format

Ruling on the two questions wq#2644's verify-pass escalated after CONTRADICTING
PR#1008 / `43d7e96f25`. Written 2026-09-25 (opus-5@high, `deps=2620`).

## The escalation was correct on fact

Re-checked independently rather than taken on the ticket's word:

| Claim | Status | Evidence |
|---|---|---|
| `_TS_FMT` was `"%Y-%m-%dT%H:%M:%SZ"` and fed both db parses | **confirmed** | pre-fix `prune_worktrees.py:54`, callers at `:208` and `:245` — the only two `_parse_ts` call sites in the file |
| the wq db stores `"%Y-%m-%d %H:%M:%S"` instead | **confirmed** | `bin/wq:145` and `bin/slock:43`, both read at pinned rev `6c1c1be`; the two writers agree, there is no second on-disk shape |
| an expired lease and an expired slock both read as *live* | **confirmed** | pre-fix probe: `wq_claim_is_live(99999) -> (True, 'status=claimed, lease=2026-09-25 08:06:56')` for a lease 90 minutes past, `slock_is_live -> (True, ...)` likewise |
| `tests/test_prune_worktrees.py:127` masked it with an ISO fixture | **confirmed** | the corrected fixture fails against pre-fix code |

One thing the escalation understated. It read as a single mis-typed constant; the
coverage hole is wider. `slock_is_live`'s expiry comparison (`:245-248`) had **no test at
all** — the one slock test seeded `expires_at = NULL`, which returns at `:244` before the
parser is ever reached. So did the live-claim test (`lease_expires = NULL`). Of the two
timestamp branches the pruner's README sells as tested behaviour, one was tested against
the wrong format and the other was not tested at all.

## Q1 — does the fix have a second-order effect on other `_parse_ts` callers?

**On `_parse_ts` callers, no. On `_TS_FMT`, yes — and that is the trap.**

`_parse_ts` has exactly two callers, both reading the db (`:208` lease, `:245` slock), and
both want the same format. But `_TS_FMT` had a third consumer that is not a parse at all:
`append_log()` (`:309`) stamps every JSONL run-log line with it. Changing the one constant
in place — the fix the escalation proposed first — would silently reformat the log's `ts`
field from `2026-09-25T09:31:41Z` to `2026-09-25 09:31:41`, mid-file, in an append-only
log that already holds 1.3 MB of ISO-8601 lines
(`/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl`). Nothing outside the pruner parses
that file today — `grep -rn` over `foreman/`, `bin/`, `kb/` finds no other reader — so the
blast radius is small, but a log that changes shape halfway through is a trap for whoever
reads it next, and the coupling that caused this bug would survive the fix.

**Ruling: split the constant.** `_DB_TS_FMT` for what the writers store, `_LOG_TS_FMT` for
what we emit. The two have no reason to be equal and every reason to be named apart.

## Q2 — should the shipped test fixtures be corrected?

**Yes, and they have to spell the format out rather than import it.**

Correcting `:127` to `'2000-01-01 00:00:00'` is necessary but not sufficient. A fixture
that reads `pw._DB_TS_FMT` would drift along with the constant it is supposed to check —
the same failure one layer up. The tests now carry their own `WQ_TS_FMT` literal with a
comment citing `bin/wq:145`, so the suite fails if the pruner and the writers ever
disagree. Added alongside: the missing expired-slock test, an unexpired-slock test that
actually reaches the parser, and a direct `_parse_ts` unit test.

## Two further calls the escalation left open

**Strict parser, not a tolerant one.** A `_parse_ts` that accepted both shapes would fix
production and re-open the hole: a future fixture in the wrong format would keep passing.
There is exactly one writer format in the fleet, confirmed at a pinned rev in both
binaries. `_parse_ts` accepts that and nothing else; `"2000-01-01T00:00:00Z"` now returns
`None`, asserted in the tests.

**A parse failure has to be audible.** The original defect was not only wrong, it was
silent — an unparseable value fell through to `"status=claimed, lease=..."`, a reason
string indistinguishable from a legitimate live claim in the run log. Both call sites keep
the fail-closed direction (unparseable → skip, never delete on a value we do not
understand) but now say `unparseable — fail closed` in the reason. A future format drift
pages in the log instead of quietly reverting to never-prune.

## What shipped

- `prune_worktrees.py`: `_DB_TS_FMT` / `_LOG_TS_FMT` split, `_parse_ts` on the db format,
  explicit unparseable branches in `wq_claim_is_live` and `slock_is_live`.
- `tests/test_prune_worktrees.py`: 13 → 17 tests; `:127` fixture corrected; local
  `WQ_TS_FMT` literal + `wq_ts()` helper; expired-slock, unexpired-slock and unparseable
  -lease cases added.
- `probe_wq2669_writer_format.py`: the acceptance probe, committed so it is re-runnable.
- `README.md`: the two-format rule and the corrected test inventory.

## Evidence

The probe takes its input from outside the population the fix was built around (wq#2630):
the tests seed their own databases and can only confirm the pruner agrees with itself, so
the probe reads the `FMT` constant out of `bin/wq` and `bin/slock` at pinned rev
`6c1c1be`, formats a 90-minute-old lease with *that* string, and asks the pruner.

```
$ python3 probe_wq2669_writer_format.py                       # fixed code
bin/wq@6c1c1be    FMT = '%Y-%m-%d %H:%M:%S'
bin/slock@6c1c1be FMT = '%Y-%m-%d %H:%M:%S'
wq_claim_is_live(99999) -> (False, 'claimed but lease expired 2026-09-25 08:06:49')
slock_is_live('probe-agent') -> (False, 'owner=probe-agent, no live slock')
PASS: both expired records read as expired            exit=0

$ python3 /tmp/.../prefix/probe_wq2669_writer_format.py       # pre-fix code, HEAD:prune_worktrees.py
wq_claim_is_live(99999) -> (True, 'status=claimed, lease=2026-09-25 08:06:56')
FAIL: expired lease reported live; expired slock reported live   exit=1
```

Same falsification for the suite — new tests against the pre-fix file:

```
$ python3 -m pytest tests -q                    # fixed
17 passed in 1.25s
$ python3 -m pytest /tmp/.../prefix/tests -q    # pre-fix code, post-fix tests
4 failed, 13 passed
  test_expired_lease_does_not_block_pruning
  test_parse_ts_reads_the_format_wq_actually_writes
  test_unparseable_lease_fails_closed_with_a_reason_that_says_so
  test_expired_slock_does_not_block_scratch_worktree
```

**The real fleet cannot falsify this, which is the point of the probe.** Dry runs before
and after the fix are byte-identical — `dry-run: scanned=1456 prunable=25 skipped=1431
size=359M` both ways — because the live db has 9 claimed tickets, 0 with an expired lease,
and 0 expired slock rows right now. `wq reap` clears abandoned leases on its own schedule
and `bin/slock` deletes expired rows on `acquire`/`list` (`bin/slock:165`, `:265-266`), so
the window where this branch matters is narrow. Narrow is not never: a worktree whose
ticket is left `claimed` by a crashed fworker between reap sweeps was exempted from
pruning indefinitely, with a reason string that read as a legitimate skip. A fleet sweep
was never going to catch that: wq#2620's own post-apply dry run passed clean on the same
day it landed (`43d7e96f25`, 2026-09-25), with the branch already dead.

## Disposition

wq#2644's verdict stands — CONTRADICTED was correct, and the fix is in this PR rather than
a follow-up ticket. The defect was a coupling (`_TS_FMT` doing two jobs) plus a fixture
that agreed with the bug, so both are gone, not just the symptom.
