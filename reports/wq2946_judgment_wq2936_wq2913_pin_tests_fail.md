# wq#2946 — verify-pass (archetype 8) of wq#2936: [rotation] burned credential 3 values set:78a555c79329 (sk-key)

Reviewer: claude-sonnet-5 @ high (fresh instance, same model as author). Worktree
`wq2946-a1`. Verified 2026-09-28. Subject: PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1246, landed on `claude-foreman`
`main` (commits `f3b7b3f`/`322ff41` "test(mask): pin replay dummies, make matrix ids
inert", `87a1daa` "test(mask): join matrix id parts with a plus sign"). `main` at HEAD
`9ecd860` already contains the PR's diff verbatim (`git diff main
origin/fix/wq2936-...` is empty for the three touched files).

## Verdict

**CONTRADICT.** The rotation verdict itself ("no rotation: all 8 wq#2913 replay
values are committed text, not credentials") is correct — `python3
harness/mask_replay.py 2913` still reports `0 unaccounted`, exit 0, right now. But the
two regression tests wq#2936 added specifically to **pin** that verdict both **fail,
deterministically, on this host, today**:

```
$ python3 -m pytest harness/tests/test_mask_replay.py -k wq2913 -v
FAILED test_wq2913_pins_are_their_hashes_and_read_silently
FAILED test_wq2913_corpus_burned_nothing
2 failed, 88 deselected in ~23s   (reproduced twice, same result both times)
```

A diff whose own safety-net tests do not pass cannot be "done" — the ticket's
acceptance bar is a green regression suite, not just a clean interactive run of the
CLI tool. Escalating per ticket instructions to opus-5@high — filed as **wq#2955**.

## 1. Failure 1 — hardcoded count is off by one against the shipped fixture

`harness/tests/fixtures/wq2913_values.py` defines `WQ2913_VALUES` with **7** tuples
(3 `sk-key` matrix-id pytest ids, 3 `sk-key` probe_sk.py outputs, 1 `url-auth`
docstring placeholder). `test_mask_replay.py`'s new test hardcodes:

```python
def test_wq2913_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ2913_FIXTURES)
    values = scope["WQ2913_VALUES"]
    assert len(values) == 8   # actual: 7
```

`AssertionError: assert 7 == 8`. The mismatch is not cosmetic: ISSUE-LOG row 924
(the row this PR closes) itself records **8** values for wq#2913 — "7 of 7 BURNED?
... plus 1 comment quoting the ids that the kb log happened to account" — so the
test's `== 8` reflects the author's own accounting of how many values needed
pinning, but the shipped `WQ2913_VALUES` tuple never grew an 8th entry for the extra
value (sha `41964b438456`, currently accounted only incidentally, via substring
overlap with the fixture's line 15 `sk-key-letter-` tuple, per
`mask_replay.py 2913`'s own `[tracked] sk-key len=16 sha256=41964b438456 ...
accounted: harness/tests/fixtures/wq2913_values.py:15` line). The fixture and the
test that pins it disagree with each other and with the author's own ISSUE-LOG note.

## 2. Failure 2 — the url-auth pin (`9fbe965f997e`) no longer reproduces under today's tree

`test_wq2913_corpus_burned_nothing` calls `_assert_corpus_pinned(2913, ...,
patterns=mask_replay.CURRENT_PATTERNS)`, which asserts `records_pin <= shas` (every
pinned hash must still be reproducible by replaying wq#2913's corpus under today's
mask patterns). It fails:

```
AssertionError: ['9fbe965f997e']
assert frozenset({...}) <= {...}
Extra items in the left set:
'9fbe965f997e'
```

Root cause, confirmed by reading source directly, not inferred: the pinned value is
`"https://x:" "gh" "p_<body>"` (fixture line 21), accounted against
`harness/tests/test_masking.py:113`. That line **used to** read
`"remote https://x:" "ghp_<body>@host"` when wq#2913 ran (matches wq#2936's own ticket
detail text: *"a placeholder in the docstring wq#2913 was writing for
masking._redundant (`remote https://x:<that>@host`)"*). It no longer does:

```
$ sed -n '112,113p' harness/tests/test_masking.py
    # wq#2931: a placeholder glued behind a token prefix (split so this file stays quiet)
    "remote https://x:" "tok_<body>@host",
```

Commit `05e65ce` ("fix(mask): skip url-auth bodies holding a placeholder", wq#2931,
2026-09-28 08:44:23Z) rewrote that exact literal from `ghp_<body>` to `tok_<body>` —
**one minute before** wq#2936's own commits landed (`f3b7b3f` 08:45:20Z, `322ff41`
08:45:51Z). wq#2936 pinned a value whose sole backing evidence in the tracked tree was
edited out from under it by a concurrent, unrelated ticket, and nobody re-ran the new
tests after the merge to catch the collision — the done-notes cite only `mask_replay
2913 exit 0` (the interactive CLI, which accounts this value differently — see below),
never a pytest run.

## 3. Why the CLI still reports 0 unaccounted while the pytest pin fails

`mask_replay.py`'s interactive report and the test's `_assert_corpus_pinned` use
different reproduction strategies: `account()` (used by the CLI's per-record
`accounted_in` field) does a same-tree substring search across all tracked text with
truncation fallbacks (`masking.untruncated`/`untruncated_whole`), so it can still find
`ghp_<body>` accounted somewhere else in the tree, or via a fallback cut, even after
line 113 changed. `_assert_corpus_pinned`'s subset check instead **replays the corpus
under CURRENT_PATTERNS and requires the pinned hash to be independently regenerated**
— a stricter, replay-based invariant exactly for catching this kind of silent drift
(its own docstring: *"a new artefact cannot move [this pin]... moved with no code
change"*). It caught real drift here; the CLI's looser accounting did not surface it.
This is not a flaky test — it is a real, working tripwire that fired on a real defect
in what was shipped as "done."

## 4. Scope check

- The actual leak-path fix (`+`-joining matrix-test pytest ids so a `FAILED` line no
  longer reads as an `sk-key` token) is sound and its own regression test passes:
  `test_case_ids_read_silently` — 1 passed.
- `mask_replay.py 2913` interactively: `8 credential-shaped values ... 0 unaccounted`,
  exit 0 — the underlying "no rotation needed" call is not disputed by this review.
- The 17 `kv-secret` candidates the tripwire suppressed (audit-only, listed in the same
  run) are unrelated to this ticket's `sk-key`/`url-auth` scope and are not re-raised
  here.

## 5. What a correct fix looks like (for the judgment pass, not applied here)

Not implementing — this is a verify-pass. For opus's judgment: (a) add the missing 8th
`WQ2913_VALUES` tuple (sha `41964b438456`, `sk-key`, prefix `sk-key-`, the 16-char body
visible at `harness/tests/fixtures/wq2913_values.py:15`) so the count assertion is
true by construction rather than hardcoded, and (b) re-pin the url-auth value against
`test_masking.py`'s current `tok_<body>` literal (new sha) — or drop that pin's
CURRENT_PATTERNS subset requirement to a legacy-table replay if the value is only
reproducible under the table wq#2913 actually ran under. Either way, run
`pytest harness/tests/test_mask_replay.py -k wq2913` clean before closing.

## Evidence trail

- `python3 -m pytest harness/tests/test_mask_replay.py -k wq2913 -v` — 2 failed, run
  twice, identical result both times.
- `python3 -m pytest harness/tests/test_mask_foreign_prefix_matrix.py -k
  test_case_ids_read_silently -v` — 1 passed.
- `python3 harness/mask_replay.py 2913` — `8 credential-shaped values ... 0
  unaccounted`, exit 0; full output cites `41964b438456` and `9fbe965f997e` records.
- `harness/tests/fixtures/wq2913_values.py` read in full via `git show
  origin/fix/wq2936-...:harness/tests/fixtures/wq2913_values.py` — 7 tuples, not 8.
- `harness/tests/test_masking.py:112-113` read directly — current literal is
  `tok_<body>`, not `ghp_<body>`.
- `git log --format="%h %ad %s" --date=iso -1 05e65ce / 322ff41 / f3b7b3f` — commit
  timestamps establishing the one-minute race between wq#2931 and wq#2936.
- `/home/lumen/foreman/_runs/wq2913/` confirmed present on this host (skip guard on
  `test_wq2913_corpus_burned_nothing` does not fire); referenced snapshot file
  `20260928T083628Z-...-harness_masking.py` confirmed to exist.
- `/home/lumen/bin/wq show 2936` — done-notes text, ISSUE-LOG row 924 read via `grep -n
  "| 924 " /home/lumen/kb/ISSUE-LOG.md`.
