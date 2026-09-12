# wq#1856 — Adversarial verify-pass of wq#1854 (fix wq#1807 despag P1-4: FileBlobStore kb-isolation escape)

**Verdict: CONFIRM** — the shipped diff (`48f107b` on `main`, cherry-picked here as
`c829d00` `fix(despag): kb-isolation escape in FileBlobStore + release session lease`,
`despag/app.py` + `despag/backing/__init__.py` + 2 new tests) fixes exactly the two
defects the original adversarial pass (wq#1851, `reports/1851_verify_wq1807_despag_p1-4_backing.md`)
found, correctly, with no regressions. PR #531's gitea API lookup 404'd (repo not visible
to an unauthenticated query from this worker); verified instead from the local `foreman`
git history, which carries the identical commit content under `Refs: wq#1854`.

## What was shipped

1. `despag/backing/__init__.py:177-184` (`FileBlobStore._p`) — the boundary check that
   was `str(p).startswith(str(base))` is now `p.is_relative_to(base)`. Same swap at
   `:214-220` in `list()`.
2. `despag/app.py:284-311` (`create_response`) — tracks `lease_fence = lease["fence"]`
   on admit and calls `CONTROL.lease_release(session_id, resp_id, lease_fence)` at the
   end of the (still-stub) handler, gated on `if lease_fence:`.
3. Two new regression tests: `test_blob_sibling_kb_with_shared_name_prefix_is_not_reachable`
   (`despag/tests/test_backing.py:94-103`) and
   `test_responses_two_consecutive_turns_of_one_session_both_admitted`
   (`despag/tests/test_uhp.py:138-147`).

## Claim-by-claim check

1. **kb-isolation escape (wq#1851 finding #2) is actually closed.** The old
   `str.startswith` check passes `"/root/kb1x".startswith("/root/kb1")` since `base` has
   no trailing separator — exactly the reproduction in the original report
   (`get("kb1", "../kb1x/secret.txt")` returning `kb1x`'s data). `Path.is_relative_to`
   does a segment-aware comparison, so `Path("/root/kb1x").is_relative_to(Path("/root/kb1"))`
   is `False`. Reproduced live in-session (scratch script, deleted after use): with the
   fixed code, `get`/`put`/`delete`/`list` on `kb1` against `../kb1x/...` all correctly
   return `None`/`False`/`[]`, and `kb1x`'s own data is untouched. Also checked cases the
   new test doesn't cover: an absolute-path `file_id` (`/etc/passwd`) still refused, and a
   symlink planted inside one `kb` pointing at another `kb`'s file is still refused —
   `.resolve()` follows the symlink to its real target *before* the `is_relative_to`
   check runs, so the escape is caught the same way. One structural gap remains
   *outside this fix's threat model*: the `kb` argument itself is not path-sanitized
   (`get("../kb1x", "secret.txt")` derives `base` from the same tainted string, so the
   check is tautological for a malicious `kb`, not just a malicious `file_id`) — but the
   original report explicitly scoped `kb` as caller-assigned and "not yet reachable over
   HTTP in this PR" (no route in `despag/app.py` calls `BlobStore` with an attacker-
   supplied `kb` yet; confirmed via `grep -n "kb\b" despag/app.py` — no matches), so this
   is not a regression or a missed case of the filed defect, just a pre-existing
   assumption the fix was never asked to change.

2. **Session lease leak (wq#1851 finding #3) is actually closed.** `lease_fence` is 0
   only when `lease_admit` itself returns `fence: 0` — checked against
   `despag/backing/control_sqlite.py:154-188`: that happens on `rejected=True` (early
   return before `create_response` reaches the release line anyway) or on the
   best-effort store-error fail-open path, both cases where `lease_release`'s own guard
   (`if fence <= 0: return`, `control_sqlite.py:222`) would no-op regardless. Every real
   admit bumps `fence` to `>= 1` (`fence = int(cur.get("fence", 0)) + 1`), so the `if
   lease_fence:` gate in `app.py` never skips a lease that was actually taken — it's an
   optimization, not a correctness dependency. `lease_release` sets `expires: 0`
   (`control_sqlite.py:232`), which makes the next `lease_admit`'s `live = expires > now`
   check false immediately, so a second real turn on the same session is admitted
   without waiting out `SESSION_LEASE_TTL_S`. No exception can land between admit and
   release in the current code — the block in between only builds a static
   `error_response` dict, no I/O.

3. **Tests: 64/64 pytest reproduced live** (`python3 -m pytest despag/tests/ -q`),
   up from the 62 the original wq#1807 PR claimed (+2 new). Both new regression tests
   are non-trivial: the kb-isolation one exercises `get`/`put`/`delete`/`list` against
   the sibling-prefix case and would fail under the pre-fix `str.startswith` check (the
   `startswith`/`is_relative_to` semantics differ exactly on this input, confirmed by
   inspection of both). The lease one issues the same UHP request body twice in a row
   through a live `TestClient` and asserts both get `501` (not `409 session_busy`) —
   this reproduces the original report's exact live repro of the bug, inverted to prove
   the fix.

## Bottom line

CONFIRM. Both defects filed against wq#1807 by wq#1851 are fixed correctly, with
regression tests that actually exercise the previously-broken paths and would fail
without the fix. No new defect found; the one adjacent gap identified (`kb` itself
untrusted) is outside this ticket's and the original finding's scope, not a
contradiction of what was shipped or claimed.
