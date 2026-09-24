# wq#2529 — lint out grep -v/-qv/-vq in Liveness: lines

wq#1975 shipped `Liveness: git show HEAD:<doc> | grep -qv <needle>`. `grep -qv` inverts
the match, so it succeeds as soon as ANY line lacks the needle — for a multi-line doc
that is true, so the probe can never go false and the ticket can never self-close.
Found by orch-batch2-sec re-verify 2026-09-24; wq#1975's own line was hand-corrected to
`grep -q`. This ticket adds the gate so `wq add`/`wq edit` refuse the shape outright
instead of relying on a human catching it in review.

## Install (Lumen)
    python3 /home/lumen/foreman/ops/wq2529-lint-liveness-grep-invert/apply_wq_patch.py   # .bak'd, idempotent; --check to verify

## What it does
`wq add --liveness ...` / `wq add --detail "...Liveness: ..."` / `wq edit --detail
"...Liveness: ..."` are refused with exit 1 when the Liveness: line's grep carries an
invert flag (`-v`, `-qv`, `-vq`, `-Ev`, ... or `--invert-match`). The error prints the
offending line and the fix: drop `-v` and use `grep -q` (probe goes true once the
needle IS present) or restructure with `grep -qL` / a negated `! grep -q` (probe goes
true once the needle is ABSENT) — `grep -v/-qv/-vq` is never the right shape. Not
`--force`-able: this is a structural bug (a check that can never go false), not a policy
call, so there is no legitimate override.

## Sweep (one-time, wq#2529)
    python3 sweep_liveness_grep_invert.py [--db /home/lumen/agents/work-queue/queue.db]

Run 2026-09-24 against the live queue: 0 open tickets carry the bug. The one line the
sweep matches (wq#2529 itself) is this ticket's own description quoting wq#1975's
historical bad probe in prose — wq#2529 is an ungated BUILD ticket (gate_type empty)
and carries no `Liveness:` tag of its own; the liveness creation gate never applied to
it. wq#1975 was already corrected to `grep -q` before this ticket was filed.

## Rollback
    cp /home/lumen/bin/wq.bak.<ts>_pre_wq2529 /home/lumen/bin/wq
