# wq#2596 — judgment: wq#2573's reflector fix, and where it actually stood

wq#2593's verify pass contradicted wq#2573/minerva PR#32's done notes. The
contradiction holds, and looking into it turned up a second problem larger than
the one the ticket was about.

## The judgment asked for

Three options were on the table: wrap `main()` in `try/except Exception`, reopen
wq#2573 for a narrower per-helper fix, or accept the residual risk.

**Chose option 1 — wrap `main()`.** Landed as minerva PR
http://100.121.17.5:3000/lumen/minerva/pulls/34, merged `5622ce2277`.

`main()` is now the guard and `_run()` the body: on an uncaught `Exception`,
write `status: "error"` with `uncaught <Type>: <msg>`, fire the same ntfy alert
every other failure path fires, then re-raise so the unit still exits non-zero
and the traceback still reaches the journal.

Why not the per-helper fix: wq#2573's mistake was implementing "every exit path"
as "every exit path I wrote". Patching `recent_episodes()` and `_post()`
individually repeats that — it enumerates coverage instead of guaranteeing it at
the boundary, and the next helper added to `_run()` is uncovered again. The
guard costs 6 lines and covers exceptions nobody has written yet.

Why not accept the risk: the failure mode is not exotic. `recent_episodes()`
opens the episode DB with a bare `sqlite3.connect()` and no exception handling
at all, and `database is locked` is a routine outcome on a fleet that writes
episodes from several processes. `_post()` indexes
`r.json()['choices'][0]['message']` on faith. `OUT_DIR.mkdir()` on `_run()`'s
first line can fail on a full disk.

`Exception` rather than `BaseException` is deliberate: `SystemExit` passes
through untouched, so `_run()`'s own `sys.exit()` sites keep their specific
state detail and a systemd stop is not relabelled as a job error.

Tests: `test_lumen_reflector.py` 8 passed, up from 5. wq#2593's uncommitted
probe is now a committed regression test, alongside one for alert-and-reraise
and one pinning the `SystemExit` passthrough. Both uncaught-exception cases were
shown to **fail** against pre-fix `b3259ca` (extracted with
`git show origin/main:lumen_reflector.py` into a scratch dir → `2 failed, 6
passed`) before passing after.

## Second finding: the fix was never live, and neither was wq#2573's

`lumen-reflector.service` runs `/usr/bin/python3
/home/lumen/minerva/lumen_reflector.py`. That tree sits on local `main`
`d6e2538`, and:

- `git grep -c write_run_state d6e2538 -- lumen_reflector.py` → **0 hits**
- `git merge-base --is-ancestor b3259ca d6e2538` → exit **1**

So wq#2573's entire mechanism — `write_run_state()` *and* the `alert()`
ntfy-swallow fix — has never executed in production. `cron_deadman.py`'s
immediate-page path has no `.state.lumen-reflector` to read at all, let alone a
stale one. wq#2593's finding was correct but understated: the ticket shipped
green and ran old code for its whole life.

The blocker is the primary tree, not the code. `lumen-pr land` cannot
fast-forward it because local `main` carries one commit origin lacks
(`d6e2538 prefs_block: wq add --pri takes int not pN`, flagged in wq#2573's own
notes and still sitting there), and `sync-live` refuses because the tree is
*also* dirty with another lane's live work in progress: ` M prefs_block.py`,
` M verify/registry.yaml`, untracked `drift_plan.py` and `drift_plans/`.

Deliberately did not force it. `guard.sh` correctly blocked
`git checkout origin/main -- lumen_reflector.py` against a primary
(wq#900/wq#929), and clobbering an active agent's uncommitted work to win a
deploy is worse than the blind spot it would close. Filed **wq#2600** [build p1]
to drain that WIP, land `d6e2538` on its own, then `sync-live`.

The generalizable hole, worth more than this ticket: **a landed PR into a repo
whose primary tree is dirty is a silent no-op deploy.** Nothing on the fleet
pages when a live tree drifts behind `origin/main`. A dirty-primary /
behind-origin audit belongs in the deadman alongside the stamp checks.

## Third finding: the same hole on the reading side, in this repo

`cron_deadman.py`'s immediate-page path was nested inside the cadence loop, so
it inherited that loop's `continue` for a unit whose timer is loaded and armed
but has never fired (`last == 0`, so no interval can be derived — the state of
every timer between a daemon-reload or reboot and its first run). A unit
reporting `status: "error"` in that window was skipped in silence.

That is the wq#2596 bug on the other side of the same pipe: the page that exists
specifically to bypass the stamp-age machinery was structurally dependent on it.
Hoisted into `check_run_states()`, which runs for any stamped unit whether a
cadence is known or not (`cron_deadman.py:132-160`, called at
`cron_deadman.py:169`).

Tests: `test_cron_deadman.py` 7 passed, up from 6.
`test_error_run_state_pages_before_a_cadence_is_known` fails against `a4ba28c`
(`sent == []`, no page) and passes here.

## What wq#2573 got right

Unchanged and still covered: the no-key, LLM-error and success exit paths, and
the `alert()` fix that logs a failed ntfy POST instead of swallowing it.
wq#2593's re-run of the original 5 tests was accurate — the suite simply never
exercised an uncaught-exception path.

## Cross-repo note

Same shape as wq#2569 and wq#2573: the dispatcher checked this ticket out as a
**foreman** worktree while half the deliverable lives in **minerva**, which this
sandbox cannot add mid-session. Applied the same workaround — a nested minerva
worktree under this worktree's gitignored `_probe/`, built and tested there,
landed via `lumen-pr`, then removed. The dispatcher still resolves one repo per
ticket; unfixed since ISSUE-LOG #192/#194.

Logged as ISSUE-LOG #716.
