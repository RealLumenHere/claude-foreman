# wq#3214 verify: wq#3207 lumen-mask-at-rest 18.8G peak / 1h18 CPU, no memory cap

Verdict: **CONFIRM** (no contradiction; no escalation)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1476 (merged as `64cdc745`,
`harness/mask_at_rest.py` / `harness/systemd/lumen-mask-at-rest.service` /
`harness/tests/test_mask_at_rest.py`)

## What wq#3207 claimed

1. Root cause was mostly **restic**, not the Python scan: it loads the offsite
   repo's whole index on every open (3877 index files, 4.6G cached), independent
   of batch size. The Python side contributed a secondary factor: the old
   `process_store` read each hit file whole and kept every file's *masked* text
   in `plans` until the restic backup batch returned, so the heap grew ~4x the
   hit-set bytes (CPython stores text containing an astral codepoint as UCS-4).
2. Fix: stream scan and mask line-by-line (`_iter_lines` replaces `_read_text`),
   never holding more than one line + a small hit-record list per file;
   `mask_file` re-reads from disk (rather than reusing pre-backup text) so lines
   a live session appends during the ~30 min restic wait are masked, not
   dropped; the restic child gets `GOGC=20` / `GOMEMLIMIT=6GiB` via
   `RESTIC_GO_ENV` (`setdefault`, so a caller's explicit env wins); the systemd
   unit gets `MemoryHigh=8G`.

## What I checked

- Read `harness/mask_at_rest.py` in full (post-merge) and the exact diff
  (`git diff e7d619b7..64cdc745 -- harness/mask_at_rest.py`) against the
  pre-fix version, plus the systemd unit and test diffs.
- Confirmed the two `masking` API calls the new code relies on match the real
  signatures: `masking.scan(text)` → `(masked_text, [hit records])` (so
  `scan_file`'s `masking.scan(line)[1]` is correct), `masking.mask_text(text)`
  → `(masked_text, [labels])` (so `mask_file`'s `masking.mask_text(line)[0]`
  is correct).
- Ran the full `test_mask_at_rest.py` suite: **19/19 pass**.
- Confirmed live deployment, not just a repo file: `systemctl show
  lumen-mask-at-rest.service -p MemoryHigh` → `8589934592` (=8GiB) on the
  actual box, and `systemctl cat` shows a matching
  `/run/systemd/system.control/.../50-MemoryHigh.conf` drop-in at the same
  value (the "runtime MemoryHigh=infinity drop-in reset to 8G" the wq notes
  describe) — no stale override left in place.
- Cross-checked ISSUE-LOG row 1100 (`kb/ISSUE-LOG.md`) against the PR notes —
  consistent, `SOLVED`.
- Verified `_iter_lines` on a synthetic `.gz` file with CRLF line endings
  preserves `\r\n` exactly (matches `test_mask_preserves_crlf_line_endings`'s
  claim, exercised independently here on a gzip-compressed source, which the
  shipped test does not cover — see below).

## Adversarial probes (wq#2630: must be able to fail)

Extracted the pre-fix `mask_at_rest.py` from `e7d619b7` into a scratch copy
outside the worktree (`/tmp/fworker-wq3214`, cleaned up after), importing the
real (unchanged) `masking.py` from the harness dir, to get an independent,
non-tautological measurement rather than trusting the shipped test's own
assertion:

1. **Memory regression, old vs. new code, same synthetic corpus** (8 files,
   ~245 KB each, one credential hit line + 250 filler lines, matching the
   shipped test's fixture):
   - OLD `process_store` (pre-fix, `--dry-run`): **peak 9,778,240 bytes**
     (9.3 MB) — exceeds the shipped test's own threshold (`corpus/8` =
     244,562 bytes) by ~40x, confirming the claimed pre-fix behavior is real,
     not a strawman.
   - NEW `process_store` (post-fix, `--dry-run`): **peak 198,177 bytes** —
     under threshold, ~49x smaller than the old code on identical input.
   - NEW `process_store` on the **real (non-dry-run) masking write path**
     (`offsite_backup` mocked to skip the network hop, `dry_run=False`,
     actual `mask_file` writes exercised): **peak 343,337 bytes** — slightly
     over the dry-run-only threshold the shipped test checks, but still ~28x
     below the old code's peak and bounded by a small constant, not by the
     number of hit files. This confirms the write path shares the same
     streaming design; it's a test-coverage note, not a correctness defect
     (below).
2. **GC-env mutation**: set `mask_at_rest.RESTIC_GO_ENV = {}` and reran
   `offsite_backup`'s env-injection logic — the resulting subprocess env has
   neither `GOGC` nor `GOMEMLIMIT`, which would fail
   `test_restic_runs_with_bounded_go_gc_and_explicit_env_wins`'s assertions.
   Confirms that test is load-bearing, not tautological.

Both probes draw their input from outside the population the shipped test was
authored against (a hand-extracted pre-fix module reimported standalone; a
hand-mutated constant) and both demonstrably fail pre-fix/mutated code while
passing the real fix — falsifiable, per wq#2630.

## Things I looked for and did not find a contradiction in

- **Shipped memory test only exercises `dry_run=True`.** The one
  tracemalloc-based regression test
  (`test_scan_memory_is_bounded_by_line_not_by_hit_set`) calls
  `process_store(paths, "t", True, {})`, which runs `scan_file` (streaming
  read for detection) but skips `offsite_backup` and `mask_file` (the actual
  write path used every real night). I measured the real write path myself
  (probe 1 above): it is also bounded by a small constant rather than the
  number of hit files, so the underlying fix is sound — but a future
  regression that reintroduced buffering *only* in `mask_file` would not be
  caught by this test. Worth a follow-up test, not a reason to contradict
  this ticket's fix.
- **`GOMEMLIMIT=6GiB` vs. unit `MemoryHigh=8G`**: leaves ~2G headroom in the
  shared cgroup for the Python process + subprocess overhead, consistent with
  the now-KB-scale Python peak measured above. Can't verify restic's own
  compliance with `GOMEMLIMIT` from this sandbox (would require running a
  real restic backup against the offsite repo, which touches production
  credentials/network and is outside a BUILD/verify worker's allowlist) — the
  wq#3207 notes already record a live post-fix run's peak as the real-world
  confirmation point; this verify pass can't force another one safely.
- **`process_store` batching**: pre-existing wq#2546 design (all dirty files
  in a pass backed up in one restic call; a bad file holds the whole batch
  unmasked) — unchanged by this diff, out of scope.
- No other caller of `process_store()` or `_read_text`/`_iter_lines` exists in
  the tree besides `mask_at_rest.py` itself and its test file — the streaming
  API swap can't have broken an external caller.

## Conclusion

wq#3207's root-cause diagnosis (restic's index-open cost dominating, Python's
whole-file buffering as a secondary multiplier) and its fix (streaming
scan/mask, bounded restic GC via env, `MemoryHigh=8G` on the live unit) hold
up under adversarial review, independent re-measurement against the real
pre-fix code, and mutation testing. No contradiction found. Confirming on the
board; no escalation to opus-5@high.
