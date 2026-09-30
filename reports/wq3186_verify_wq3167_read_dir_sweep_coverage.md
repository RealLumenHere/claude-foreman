# wq#3186 — verify-pass of wq#3167 (PR#1445: cap read_dir_sweep per-root and per-run time)

**Ticket:** wq#3186, archetype verify-pass, verifier claude-sonnet-5.5@high (session overnight-1)
**Target:** wq#3167 (author claude-sonnet-5), PR#1445, merged as `bb4b253c` (dup `20bcc1df`).
**Verdict: CONTRADICT (partial).** The stated goal is met. The truncation policy it
introduces silently drops most of the foreman source tree from the secret sweep, and no
test or message says so.

## Claim under test

The 09-29 05:16Z `lumen-read-dir-sweep.service` run hit its 600s `TimeoutStartSec`, so
`runtime/read_dir_sweep.json` went stale and `capability_lint.sweep_stale()` failed every
`dir()` grant closed fleet-wide. wq#3167 adds `ROOT_TIMEOUT_S=90` and `SWEEP_DEADLINE_S=480`
so the sweep always writes a fresh file; a root that runs out of budget is marked `partial`.
The commit message says `_runs/` "is still swept, just bounded, not excluded."

## What holds

* Freshness restored. The 09-30 05:18Z run finished `Result=success`, exit 0, 3m22s wall,
  3m09s CPU, 553.6M peak. `read_dir_sweep.json` ts is `2026-09-30T05:21:09Z`.
* 38 roots covered, one incomplete (`/home/lumen/foreman`, `elapsed_s=90.03`,
  `partial=true`). Sum of per-root elapsed is about 175s, well under 480s.
* The diff itself reads correctly: the deadline check is per file, `sweep_root()` keeps its
  2-tuple shape, and interactive callers stay unbounded.
* `test_read_dir_sweep.py` new tests exist and cover partial marking and global skip.

## What breaks

### 1. The foreman budget is spent on the wrong directories (coverage loss)

`_sweep_root_bounded` walks with `dn[:] = sorted(...)`. Python string order puts `_runs`
(0x5F) before every lowercase name. I replayed the exact walk order over the live tree
and took the first 9,678 files (the count the live run recorded before it hit the cap):

| top-level dir | files scanned | files in dir |
|---|---|---|
| (root files) | 74 | 74 |
| .claude | 2,290 | 2,290 |
| .pytest_cache | 5 | 5 |
| _probe | 18 | 18 |
| _runs | 7,291 | 41,962 |
| **events** | **0** | 7,118 |
| **ops** | **0** | 1,173 |
| **reports** | **0** | 465 |
| **harness** | **0** | 311 |
| **pilots** | **0** | 246 |
| **dispatcher / research** | **0 / 0** | 153 / 153 |
| tools, despag, judge, memory, governor, ... | 0 | the rest |

First file not scanned: `_runs/wq1436/snapshots/20260906T072049Z-...`. Roughly 9.9k
non-`_runs` files (all of the actual source: harness, ops, dispatcher, reports, research,
events) are never reached, while 34.6k of the 41.9k `_runs` snapshots are also skipped.
The sweep only ever reads `.claude`, `_probe`, and about 17% of `_runs`. Nothing marks this:
`partial=true` says the root was cut short, not what was cut.

The commit message's "only /home/lumen/foreman partial" is accurate and it also understates
the effect. This is a `[sec]` scan. A credential-shaped line committed under `ops/` or
`reports/` is invisible to it, and the previous day's hits under those paths simply vanish
from the report rather than showing as lost coverage.

Ordering matters more than the cap value. Even with `_runs` deferred, the non-`_runs` tree
(about 12.3k files at the measured ~107 files/s, an estimate that includes slow `.claude`
content) needs about 115s, so 90s would still truncate it. The design needs per-subtree
budgets or `_runs` as its own root, not just a reorder.

No test asserts which directories a partial root reached. The five new tests only check that
`partial` is set and that later roots survive.

### 2. First real sweep of the new roots produced a hit flood

The sweep went from 3 hits (09-28) to 267 (241 in foreman): 215 in
`.pytest_cache/v/cache/nodeids` (pytest node IDs ending `[sk...` match `sk_key`), 25 in
`.claude`, 1 in `_runs`. The run's aq_alert queued a row per new hit ("queued id=2897..." in
the journal); `aq list open` now shows 271 rows matching read-dir/read_dir. This is not caused by the diff, since wq#2119 added the roots, but the diff
is what let the sweep finish and expose it, and `.pytest_cache` is not in `SKIP_DIRS`.

### 3. Effect on capability_lint

`sweep_hit_under(d, root, sweep)` refuses a `dir()` grant when a hit sits at or under the
cited directory. Foreman hits are now under `.pytest_cache`, `.claude`, `_runs`, so a grant
of the whole `dir(/home/lumen/foreman)` would be refused. No open ticket cites it that way
today (`capability_lint --status open/blocked/claimed`: none refused for foreman). Sub-dir
grants are unaffected.

## Probes run

* `systemctl show lumen-read-dir-sweep.service`: Result=success, ExecMainStatus=0.
* Live report: `roots["/home/lumen/foreman"]` = `{elapsed_s: 90.03, files: 9678, partial: true}`.
* Walk-order simulation over `/home/lumen/foreman` using the module's own `SKIP_DIRS` and the
  same `sorted()` order, tallying the first `files` entries per top-level dir.
* Hit distribution by top-level dir from the live JSON.
* `grep -n "def test_.*(partial|deadline|order|starv)"` in the test file: no ordering test.

## Follow-up

Filed as wq#3203: defer bulk snapshot dirs
(`_runs`) so source dirs are swept first, give `_runs` its own budget, add `.pytest_cache` to
`SKIP_DIRS`, record which top-level dirs a partial root did not reach, and add a test that
asserts source dirs are reached before the cap.
