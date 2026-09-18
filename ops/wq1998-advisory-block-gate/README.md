# wq#1998 — the liveness creation gate is ADVISORY at `wq block`

## What happened

wq#1676 made a `Liveness:` line mandatory at the creation on-ramp, and `cmd_block` shares
that on-ramp. `_liveness_creation_check` refuses a missing tag with `eprint(REFUSED ...)` +
`sys.exit(6)`, which returns BEFORE `cmd_block` reaches `_finish(a, 'blocked')`. So
`wq block <id> "<free-text reason>"` — what every worker issues when it hits a BLOCKED
sentinel — did not block anything. The row stayed `claimed` until its lease expired, under a
caller that had already given up and exited.

An untagged blocked row is still findable: `liveness_clear.age_alarm()` files an aq row for
it after 7 days. A stranded claimed row is findable by nothing. A retreat must always land.

## The fix

`_liveness_creation_check` takes `fatal=True`. `cmd_block` is the only caller that passes
`fatal=False`, and on that path the refusal becomes a warning
(`liveness NO_TAG at block: ... (advisory — NOT enforced at block, wq#1998)`) and the block
files. `add` and `decide` are untouched and still exit 6.

The advisory path deliberately does NOT stamp `[NO-PROBE: ...]` into the detail. That marker
is what `age_alarm()` skips on, so stamping it on every automated block would silence the
7-day stale-gate alarm for exactly the population it exists to catch
(`harness/fworker.py:576-589`).

## Why this keeper exists (wq#2015)

wq#1998 was applied to the live `/home/lumen/bin/wq` and never committed to the bin repo.
wq#1987's content-divergence sweep reconciled the file back to the repo at 2026-09-18
16:30:20 and silently reverted it, taking 17 harness tests red on one cause
(`assert 'claimed' == 'blocked'`). The patch is committed now — lumen-bin PR #97, squashed as
`9ba6721` — so a sweep no longer reverts it. This keeper is for the other failure mode: `wq`
is rewritten in place by whichever lane touches it next (ops/wq841's lesson), and a
copy-and-patch lane can still drop it.

    python3 ensure_wq_advisory_block_gate.py --check --json   # is the live wq patched?
    python3 ensure_wq_advisory_block_gate.py --apply          # backup, verify, roll back on failure

`--apply` refuses unless each of its 4 anchors matches exactly once, and its `verify()` proves
both halves on a scratch queue: an untagged `block` lands (rc 0, row `blocked`, advisory
warning, no `[NO-PROBE:]` stamp) and `add --frank-gated` with no tag still refuses with rc 6.

The alarm is `harness/tests/test_wq1998_advisory_block_gate.py`; the behavioural contract is
`harness/tests/test_liveness_clear.py::test_block_creation_gate_and_liveness_flags`.
