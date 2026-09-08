# wq#1479 — a close verdict you cannot miss

On 2026-09-07 a session reported wq#1133 and wq#1296 closed after running
`wq done --no-artifact`. Neither was closed: both are still `blocked`, `done_at` NULL,
`reopened_count` 0. `wq done` had refused and exited 1; the session read `tail -1` of the
output and saw the last line of the refusal's explanation, which reads like an ordinary
trailing note.

Class: **a state-changing CLI verdict was never read.** The exit code was there and unread;
the refusal was on stdout, where a tail cannot tell it from an answer.

Three things now stand between that report and the record.

| what | where |
|---|---|
| Close-path refusals print to **stderr**, so `tail -1` of stdout cannot show one at all | `ensure_wq_refuse_stderr.py` (patches `/home/lumen/bin/wq`) |
| `wq_close` — claim-then-done, gated on every exit code, **confirmed against the ticket row**, last line is always `CLOSED` / `NOT-CLOSED` | `harness/wq_close.py` |
| Both pinned by tests, including an alarm that fires when the live `wq` loses the patch | `harness/tests/test_wq1479_close_verdict.py` |

## The wq patch

Scoped to the close path — `_finish` (done/release/block), `cmd_done`'s wq#759 and wq#899
gates, `owner_ok`, `cmd_heartbeat`, `cmd_hold`. `cmd_claim`'s refusals stay on stdout on
purpose: `/home/lumen/bin/probe_wq582.py` and `probe_wq594.py` assert them there, and moving
them buys nothing this ticket is about.

```
python3 ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py --check --json
python3 ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py --apply
python3 -m pytest harness/tests/test_wq1479_close_verdict.py
```

`--apply` is anchor-based and idempotent: it backs up, patches a candidate copy, proves it
on a scratch queue (a refused close must put REFUSED on stderr and nothing on stdout, an
ordinary close must still work), and only then writes the live binary — rolling back if the
live copy fails the same check. It refuses outright if an anchor no longer matches exactly,
rather than half-patching a `wq` that has moved on.

Applied 2026-09-08. `wq` is untracked-in-practice and lanes rewrite it in place from older
copies — that erased the wq#841 patch six hours after it landed. `test_the_live_wq_is_still_patched`
is the alarm; `--apply` is the repair.

## The wrapper

```
python3 harness/wq_close.py 1133 --notes "..." --no-artifact "liveness: <detail>"
python3 harness/wq_close.py 1133 --notes "..." --json
```

Exit 0 only when the row says `done`. It owns the sequence that is not obvious: `wq done`
on another session's live claim is refused by `owner_ok`, and `wq claim --id` refuses a
`blocked` ticket outright (`wq:1245`), so closing a blocked frank-gated ticket means
unblock -> claim -> done. If any step after the unblock fails, the original block is
restored — a wrapper that leaves a frank-gated ticket `open` because its close failed has
quietly widened the queue.

Anything closing a ticket it does not hold should go through it. `harness/fworker.py`'s
`wq_done` already gates on the exit code and raises (`fworker.py:514`); it holds its own
claim, so it needs nothing from here.

## Not done here

wq#1133 and wq#1296 stay blocked. Closing them is Liveness Gate Slice 3, which is gated on
Frank (`research/LIVENESS_GATE.md` §10) — and Slice 3's close path is `wq_close`.
