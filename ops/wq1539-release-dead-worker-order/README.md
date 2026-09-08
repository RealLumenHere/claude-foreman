# wq#1539 — `wq release --dead-worker` printed REFUSED before it evaluated the override

## What happened

work-1536 (first live use of wq#1498's release path) ran `wq release 1350 --dead-worker`.
It succeeded — holder `fworker-wq1350` verified dead, released on the record — but the
output opened with the full `REFUSED: wq#1350 is held by ...` block anyway, followed by the
WARNING + `released` lines. `_finish` (harness for `wq release`/`wq done`/etc.) called
`owner_ok()` first; `owner_ok()` eprints its `REFUSED: wq#<id> is held by ...` banner
unconditionally on refusal, BEFORE `_finish` got a chance to evaluate the wq#1498
`--dead-worker` override. A caller reading only the first line, or a harness grepping stderr
for REFUSED, concludes the call failed when it didn't.

## The fix

`_finish` now checks the override's own eligibility (holder set, holder != caller,
`holder_is_dead()`) BEFORE calling `owner_ok` at all, not after. When the holder is verified
dead, the override applies and `owner_ok` is never called — so it never gets a chance to
print. `owner_ok` itself is untouched: no signature change, no new parameter. Every other
caller (a plain release/done, `cmd_heartbeat`) is unaffected, including the `--dead-worker`
case where the holder turns out to still be alive — that path still calls `owner_ok` and
still gets refused, same as before, plus the override-specific "liveness check found it
still alive" message.

Same "ensure script" shape as `ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py`
and `ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py`: hand-patches a known text anchor in
the deployed `/home/lumen/bin/wq`, verified against a scratch queue and a stubbed `lumen-run`
before it goes anywhere near the live binary, with backup + rollback on failure.

Deliberately does NOT touch `owner_ok`'s body or signature — only `_finish`'s call order — so
`ops/wq1498`'s and `ops/wq1479`'s own keepers, which anchor text inside `owner_ok`, stay valid
unchanged. `ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py`'s own
`FINISH_HEAD_NEW` anchor DOES move (it's the exact block this patch reorders), so that
constant was updated in place to the post-wq#1539 text — applying wq#1498 fresh now also
carries this fix, rather than leaving a stale, no-longer-live anchor as a false "lost the
patch" alarm.

    ensure_wq_release_dead_worker_order.py --check [--json]   is the live wq patched?
    ensure_wq_release_dead_worker_order.py --apply            patch it (backup, verify, roll back on failure)

## Verification

- `python3 ops/wq1539-release-dead-worker-order/ensure_wq_release_dead_worker_order.py --apply`
  — applied live, backed up to `/home/lumen/bin/wq.bak.wq1539.<timestamp>`.
- `python3 -m pytest harness/tests/test_wq1539_release_dead_worker_order.py
  harness/tests/test_wq1498_dead_worker_release.py harness/tests/test_wq1479_close_verdict.py`
  — 24 passed.
- Same wq-hunks caveat as wq#1538/wq#1539's own ticket text: the deployed `/home/lumen/bin/wq`
  is not the same file as `origin/main`'s tracked source (there isn't one — `wq` itself lives
  outside git); this ops/ script is the source of truth checked into the branch, applied
  directly to the deployed binary.
