# wq#2970 — verify pass on wq#2962 (rotation row sk-key x3, `30674cfcb5c5`)

**Verdict: CONFIRM.** wq#2962's fix is correct and already landed on `main`.

## What wq#2962 claimed

The wq#2275 mask tripwire fired on wq#2955 attempt 1 for three `sk-key`-shaped
values (sha 12-hex `ba8aad54215e`/`b4ef69356f53`/`f3a0c6e016c9`). `mask_replay.py
2955` exits 0, 0 unaccounted: all three are the committed wq#2913 fixture dummies at
`harness/tests/fixtures/wq2913_values.py:15,19,20`. Root cause: wq#2955 ran the
red `test_wq2913_pins_are_their_hashes_and_read_silently` (its `assert len(values)
== 8` was stale — the fixture holds 7), and pytest's assertion-rewrite prints the
full argument tree of a failing assert, so the failure line showed `where 7 =
len((('ba8aad54215e', 'sk-key', 'sk-key-letter-', 'sk-key-letter-' + 'xoxb--off1-' + 'masking'), ...))` — the raw dummies, in the model's own context. Every
`*_pins_are_their_hashes`/`*_corpus_burned_nothing` test in the file had the same
shape (`assert value_sha12(value) == sha` / `assert account(value, ...)` inside a
loop over the fixture tuple), so any red pin re-arms the same leak.

Fix (PR#1266, commit `751de7e` on the PR branch / `91148f7` on `main`, file-for-file
identical): a new helper `_unpinned(pins, tree=None)` in
`harness/tests/test_mask_replay.py` returns only the **shas** of pins that fail to
verify, never the value; every one of the ~13 call sites that used to loop-and-assert
over raw fixture tuples now routes through it, and every bare `assert len(values) ==
N` became `count = len(values); assert count == N` so pytest's rewrite has nothing
but an int to print. A new regression test,
`test_a_failing_pin_names_hashes_not_values`, breaks one pin's sha on purpose and
asserts the report is exactly `["000000000000"]` and that `mask_text` finds nothing
in it.

## What I checked

1. **PR == main**: `git diff 91148f7 751de7ec -- harness/tests/test_mask_replay.py`
   is empty — the merged commit is byte-identical to the PR commit. Diff is
   additive/rewrite-only in one file (100 lines, 60+/40-), no production code
   (`mask_replay.py`, `masking.py`) touched.
2. **No leftover vulnerable shape**: `grep -n "value_sha12(value) == sha\|account(value,\|assert len(values)"` over the current file finds zero live call sites outside the docstring — every site cited in ISSUE-LOG #947 was actually converted, not just the one that fired.
3. **Read `_unpinned` for correctness**: handles both pin shapes, `(sha, label,
   value)` and `(sha, label, prefix, whole)` — `prefix = pin[2] if len(pin) == 4
   else ""`, `body = whole[len(prefix):]`, checks `whole.startswith(prefix)`,
   `value_sha12(body) == sha`, and (if `tree` given) `mask_replay.account(body,
   tree, prefix)`. On any failure it appends only `sha` — never `body`/`whole` —
   so no failure path can leak a value, regardless of which of the three checks
   trips.
4. **Adversarially reproduced the original leak, and its absence in the fix**
   (synthetic probes in `/tmp/fworker-wq2970`, deleted after; not part of the
   verify repo):
   - Old shape (`assert len(values) == 8` directly on the loaded `WQ2913_VALUES`
     tuple): pytest's own failure output printed
     `where 7 = len((('ba8aad54215e', 'sk-key', 'sk-key-letter-',
     'sk-key-letter-' + 'xoxb--off1-' + 'masking'), ...))` — the live leak, reproduced
     byte-for-byte against ISSUE-LOG #947's cited line.
   - New shape (`assert _unpinned(broken_pins, tree) == [<wrong>]` with one pin's
     sha deliberately corrupted): pytest's failure output printed only
     `assert ['000000000000'] == [...]` — no dummy body anywhere in the traceback.
   This is the mechanism, not just the unit test's self-check — real pytest
   assertion-rewrite output, not `_unpinned`'s return value inspected in-process.
5. **Ran the new/touched tests directly**: `test_a_failing_pin_names_hashes_not_values`
   and `test_wq2913_pins_are_their_hashes_and_read_silently` both pass (2 passed,
   0.28s).
6. **Full-file suite**: `harness/tests/test_mask_replay.py`, 93 tests, 240s — 92
   passed, 1 failed. The failure is `test_wq2913_corpus_burned_nothing`
   (`records_pin <= shas` fails on the same 7 shas), which ISSUE-LOG #947 itself
   already names as a known, separate casualty of commit `960404a`
   ("mask the union of every rule's spans", landed the same day, changes which
   shape the replay assigns those values under `CURRENT_PATTERNS`) — not something
   this diff touches or could fix, and not part of what wq#2962 was scoped to.
7. **Reran the actual acceptance probe independently**: `python3
   harness/mask_replay.py 2955` → exit 0, "1 credential-shaped values in the run's
   context, 0 unaccounted", the one tracked value `[tracked]` at
   `harness/tests/fixtures/wq2913_values.py:15`. (Only 1 of the original 3
   reproduces today because of the same `960404a` side-finding #947 already logs —
   the "0 unaccounted" verdict, which is what the rotation row is actually gated
   on, holds either way.)
8. **Scope check**: grepped the rest of `harness/tests/*.py` for the same
   loop-over-fixture-tuple shape — no other file has it, so this wasn't a partial
   fix of a wider pattern.

## Conclusion

wq#2962's root-cause read is correct and independently reproduced (the old assert
shape really does leak the dummy through pytest's rewrite), the fix closes it
completely for every call site in the file, the new regression test pins the exact
failure mode, and the "0 unaccounted" rotation verdict replays clean today. The one
failing test in the full run is pre-existing, already logged (ISSUE-LOG #947), and
attributed to an unrelated same-day commit. No contradiction, no escalation.
