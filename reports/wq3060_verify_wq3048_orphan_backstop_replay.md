# wq#3060 verify-pass: wq#3048 [rotation] burned credential sha256:72458136d6c6 (aws) — CONFIRM

**Verdict: CONFIRM.** PR 1342 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1342, landed as
`4b4e69f7`) does exactly what wq#3048 claims: `mask_replay.py` was structurally blind to
`masking._orphan_matches()` rows (the wq#2922 backstop's own alarm class), so a run whose tripwire
paged on an orphan — including this ticket's own value, aws `sha256:72458136d6c6` from wq#3037's
overfire probe — could get "0 unaccounted" without the replay ever having scored it. The fix reads
the backstop via a new `masking._orphan_spans()` (offset-carrying twin of `_orphan_matches()`) and
`mask_replay._located()`, and adds an audit-only `unreproduced()`/`report_unreproduced()` path that
names a recorded hit the replay still can't reproduce instead of dropping it silently. Nothing
needs rotating: the value was a synthetic adversarial probe string, and a separate, earlier
same-day fix (`391a11b8`, "orphan backstop's trailing run must not re-open on a prefix") already
narrowed the rule so this exact chain no longer reads as a credential under today's table at all —
which is why it now surfaces as *unreproduced audit*, not as a live unaccounted hit demanding
rotation.

## What the PR does

Diff (`4b4e69f7`, 5 files, +149/-14):

- `harness/masking.py`: `_orphan_matches()` becomes a thin wrapper over new `_orphan_spans()`,
  which returns `(label, prefix, value, lo, hi)` — the same rows, plus the raw span each was cut
  from. Needed because an orphan's `value` is `before + after` joined across the mask literal, so
  it is not a substring of the source text and can't be located by string search.
- `harness/mask_replay.py`:
  - `_located()` (new): when replaying the current table with masking on, yields
    `masking.read(text)` (now span-carrying; previously `masking.raw_matches(text)[0]` threw the
    spans away — `raw_matches` is itself just `read()` minus spans, so this is not a coverage
    change) **plus** `masking._orphan_spans(text)` — the actual fix. `_hits()` becomes a thin
    wrapper that drops the span for callers that don't need it.
  - `_context()`: splices every orphan on the line (not just the target hit) out before masking,
    right-to-left by span start, so a context window never prints a neighbouring orphan in the
    clear — `mask_text()` never masks an orphan by design (that's the backstop's contract).
  - `replay()`: threads `lo`/`hi` through to build the line window from the actual span rather
    than a value substring search.
  - `unreproduced(ticket_id, records)` / `report_unreproduced()` (new): diffs the run's own
    `mask-hits-*.json` against what this replay reproduced; anything recorded but not reproduced
    (a since-narrowed rule, or an unreplayable source) prints as an audit line, never touches the
    exit code (`unaccounted()`/`report()` are untouched by this path).
- Fixtures: `wq2922_values.py` gains a 4th pinned value (a Q-filled `ghp_` orphan that *does* still
  reproduce today); `wq3037_values.py` gains a comment explaining its ten context-less mask-hits
  rows (incl. `72458136d6c6`) are deliberately *not* pinned — today's table no longer produces them
  at all, so they belong in the new unreproduced-audit list, not in `WQ3037_VALUES`.
- `test_mask_replay.py`: two new tests cover the orphan-scoring + splice behavior and the
  unreproduced-listing behavior directly.

## Reproduction — the actual probe, before and after

Built a scratch `git worktree add --detach` at `4b4e69f7^` (the pre-fix parent, `d9820239`) to run
the identical probe both sides of the fix, against wq#3037's real on-disk run corpus
(`_runs/wq3037/`), removed after:

```
$ python3 harness/mask_replay.py 3037     # PRE-FIX (d9820239)
wq#3037: 5 credential-shaped values in the run's model context, 0 unaccounted
   ...(sha256:72458136d6c6 does not appear anywhere in the output — no unreproduced section exists)
$ echo exit=$?
exit=0

$ python3 harness/mask_replay.py 3037     # POST-FIX (4b4e69f7, main)
wq#3037: 5 credential-shaped values in the run's model context, 0 unaccounted
...
wq#3037: 10 hit(s) the run's tripwire recorded that today's table does not reproduce — a
since-narrowed rule or an unreplayable source; audit only:
  github sha256=870b480040bc
  sk-key sha256=0bc654921d14
  slack sha256=00761f53a2b4
  aws sha256=457643f44d19
  cloudflare sha256=09afaafa7c0d
  github sha256=1960aa36bab6
  cloudflare sha256=44da8897fcc4
  sk-key sha256=484d412fddca
  slack sha256=e207a241c11d
  aws sha256=72458136d6c6          <-- this ticket's value, now visible
$ echo exit=$?
exit=0
```

This is the falsifiable core of the claim (wq#2630): pre-fix, the ticket's own value is invisible
in the report with no trace of it anywhere (the exact bug wq#3048 describes); post-fix it is named
explicitly, in the audit section the PR added, with the exit code correctly unchanged (this value
doesn't reproduce under today's rules at all, so it's audit, not a live rotation demand — matches
`kb/ISSUE-LOG.md:1148` row 1007's disposition, "Nothing rotated").

## Test suite

```
$ python3 -m pytest harness/tests/test_mask_replay.py -q
100 passed in 322.19s

$ python3 -m pytest harness/tests/test_masking.py -q
1 failed, 137 passed
FAILED test_masking.py::test_the_mask_suite_does_not_arm_its_own_tripwire
  — reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md carries a whole sk-ant- shape (len 20)
```

Confirmed pre-existing and unrelated: same failure, same file, reproduces identically against the
pre-fix worktree (`4b4e69f7^`). Already flagged in `kb/ISSUE-LOG.md:1148` row 1007 as "Unrelated,
pre-existing on main" — not introduced by this diff, out of scope for wq#3048.

## Adversarial angles tried and resolved (not contradictions)

- **Is `masking.read(text)` a narrower source than the `raw_matches(text)[0]` it replaces?** No —
  `raw_matches()` (`masking.py:1047`) is `read(text)` with the spans stripped before return; the
  new code calls `read()` directly and keeps them. Identical (label, prefix, value) coverage,
  spans added, not swapped.
- **Could a value that still reproduces under today's rules get miscategorized into the new
  audit-only `unreproduced()` bucket and dodge `unaccounted()`'s exit-1 path?** No —
  `unreproduced()` only lists sha12s *not* in `have` (what this replay's `_located()` actually
  produced for the ticket). Anything that does reproduce stays in `records` and is scored by the
  existing, unmodified `unaccounted()`/`report()` path. The new path can only ever add visibility,
  never remove accountability.
- **`_context()`'s right-to-left orphan splice — could it double-process the target hit if the
  target itself is an orphan?** No — `_context()` first does `line.replace(value, marker)` for the
  target, then re-runs `masking._orphan_spans()` on the already-substituted line; the target's
  literal span is gone by then, so it can't be rediscovered and re-spliced.

## Disposition

`wq#3060` closes CONFIRM. PR 1342 stands as shipped (already on `main` as `4b4e69f7`); nothing to
rotate, nothing to re-implement.
