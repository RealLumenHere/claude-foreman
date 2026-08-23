# wq#751 ops-audit A1 — worker success rate vs max_turns

`fworker_stats.py` mines the per-attempt records `fworker.py` writes to
`/home/lumen/foreman/_runs/wq*/attempt-*.json`, joins them against the wq task
metadata in `queue.db`, and prints the markdown that becomes
`reports/opsaudit_a1_worker_success.md`.

## Regenerate the report

```
python3 ops/wq751-worker-success/fworker_stats.py > reports/opsaudit_a1_worker_success.md
```

Read-only against both sources. `--runs-glob` and `--db` override the defaults,
which is how the tests drive it against synthetic datasets.

## Run the tests

```
python3 -m pytest ops/wq751-worker-success/test_fworker_stats.py
```

## The rule these tests enforce

Originally this script only recomputed its tables. The surrounding prose was
prose written once, against the 69-ticket/149-attempt dataset of 2026-08-22, and
baked into string literals. Rerunning it in the way the report header advertises
therefore produced a document that argued with itself: §6 printed
"`verify-pass` ... ha[s] zero measured successes in this dataset" directly
beneath its own freshly-rendered table showing eight `verify-pass` successes
(wq#839, escalated from wq#776's verify-pass of PR#46).

So: **every count, rate, range and comparative claim this script emits must be
derived from the attempts loaded on that run.** That includes sentences, not
just table cells. A conclusion that cannot survive its own dataset changing
under it does not belong in a generated report — express it as a computed
branch, or leave it out.

Refreshing a stale literal to today's snapshot is not a fix. It reproduces the
same bug on the next rerun, which is why `test_fworker_stats.py` builds
datasets whose correct answers are the *opposite* of the live ones and asserts
the narrative followed the data.
