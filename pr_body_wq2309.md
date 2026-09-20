## What

One test: drive `run_child` end to end with a real `--add-dir` on the child's argv and
assert the same stream pages or stays quiet purely on whether the repo holding the text
was granted.

Opened from wq#2309, a rotation ticket the wq#2275 tripwire filed against wq#2294
attempt 1 on the `sk-key` label. The investigation half of that ticket found no burned
credential; the screening fix it pointed at landed in parallel as wq#2319
(`be9994d`), so all that is left here is the coverage that fix does not have.

## Why

### Nothing was burned

No snapshot of the run carries the label — the earliest `sk-key`-bearing snapshot
freezes at 22:32:31, two minutes after the ticket exists — so the trigger was pinned
from the CLI session transcript in timestamp order under `mask_replay.LEGACY_PATTERNS`
(the rules live at 22:30). Exactly one `sk-key` match precedes the ticket's
`created_at`: the `tool_result` at 22:30:02.256Z, one second before the alarm.

The command behind it is an instinct-4-correct census — the era rule grepped across
`/home/lumen/kb` and the worker's own worktree, piped through
`/home/lumen/bin/mask-secrets`, then counted. Of the 15 distinct entries in the
histogram, 14 end in the mask literal and one does not: prefix `sk-rout`,
`value_len=16`, `sha256[:12]=c5df24610b0a`.

That survivor is the house masker's own output. `mask-secrets` reduced a 33-char
hyphenated phrase (`raw sha256[:12]=4c2234c56054`, tracked at
`reports/1227_verify_wq1223_task_dedupe.md:8` — a report title, no credential) to a
kept prefix plus the mask literal, and the era rule re-matched the result because its
greedy prefix group backtracks until 16 body characters remain, with the 6-character
mask literal supplying the tail. Feeding that source file back through `mask-secrets`
regenerates the survivor, same body hash. The raw phrase appears nowhere in the
transcript at any timestamp: only the masked form ever reached the context. At HEAD the
same value scores 0 records through `masking.scan()`.

Verdict: false positive, nothing rotated. Residual, owned by wq#2313:
`/home/lumen/bin/mask-secrets` still re-arms on its own output, and no build worker may
edit that primary tree.

### The gap this fills

wq#2319 widened provenance to every granted root, and that fix was verified here
against the live corpus rather than assumed: of the credential-shaped tracked files in
`/home/lumen/kb` and `/home/lumen/bin`, 3 page when screened against the worktree alone
(`bin/lumen-github-identity.sh`, `bin/probe_wq616.py`,
`bin/tests/test_wq_done_gate_terms.py`) and 0 page through
`provenance_roots(cwd, granted_dirs(cmd))`.

What its tests do not pin is the wiring: the grant is read off the argv inside
`run_child`, and every existing test calls `classify` / `provenance_roots` directly.
Passing `cwd` there again — the exact bug wq#2319 fixed — would leave all of them green.

## How

`run()` takes `extra_args`, which ride on the child's argv the way `--add-dir` rides on
the CLI's: ignored by the stub child, read back by `run_child`. The new test commits a
credential-shaped fixture into a throwaway repo, then runs the identical stream twice.
Ungranted it pages; granted it does not; masking is asserted unchanged in both. The
paging run is the control, so the test cannot pass vacuously.

## Testing

`pytest tests/test_burn_filter.py tests/test_masking.py tests/test_mask_replay.py` —
69 passed, 1 skipped.

## Risk / rollback

Test-only; no production path changes. Rollback is reverting this commit.

Refs wq#2309. ISSUE-LOG row 593 carries the full forensics and the method note for the
next ticket of this class.
