# wq#4531 verify-pass: wq#4528 planner.py manifest/HAL type guards

**Target:** PR `claude-foreman#2384` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2384),
merged as `ce7b8c8f` on `main` ("fix(wq4528): guard manifest and hal field types in planner").
Author: claude-opus-5. Verifier: claude-sonnet-5@high (fresh instance, wq#4531).

**Verdict: CONFIRM.**

## What the diff claims

wq#4528 is the judgment ticket on wq#4520's CONTRADICT of wq#4509's `limpet/planner.py`:
`manifest.json`'s `identity`/`hardware_bound` fields were read without the `isinstance`
guards two sibling modules (`workspace_report.py`, `agent_tui.py`) already apply, so a
wrong-but-valid JSON shape crashed `build_system_prompt()` despite its own "never raise"
docstring contract. The judgment picked option (a)+(b)-bounded: apply the sibling guards
as a narrow mechanical fix, *and* audit the whole 116-line module for the same pattern.
wq#4528's done-notes claim the audit found 7 raising shapes (not wq#4520's 3): `identity`
as list/str/int, `hardware_bound` as str/dict/non-dict-entries, `hardware.json` non-dict
list entries, non-str `descriptor` (`TypeError` in `mask_text`), and non-str `capabilities`
entries — plus an `OSError` guard on `read_blocks` "for contract symmetry". Claimed: 16 new
tests, 14 fail against pre-fix `planner.py`, 25/25 pass in `test_planner.py`, 751/751 across
`limpet/`.

## What I checked

- Read `limpet/planner.py` (132 lines, post-fix) and the `test_planner.py` diff cold.
- Diffed the worktree's pre-staged commit against the PR's merge commit byte-for-byte
  (`git show c8a5f9f1:limpet/planner.py` vs `git -C /home/lumen/foreman show
  ce7b8c8f:limpet/planner.py`) — identical. The worktree carries the actual shipped diff,
  not a restated copy.
- Line-by-line matched every claimed fix against the code:
  - `identity`: `man.get("identity") if isinstance(man.get("identity"), dict) else {}` —
    guards list/str/int/float/bool/None (`planner.py:107`).
  - `hardware_bound`: `... if isinstance(..., list) else []`, then each entry re-checked
    `isinstance(h, dict)` before `.get()` — guards both the container and malformed entries
    (`planner.py:109-112`).
  - `hardware.json` non-dict entries: `_read_hal_inventory` now filters
    `[d for d in data if isinstance(d, dict)]` (`planner.py:91-93`).
  - `descriptor`: `wr.mask_text(str(d.get("descriptor") or ""))` — explicit `str()` cast
    before `mask_text`, so a non-str descriptor (e.g. `hardware.json` hand-edited to carry
    an int) can no longer hit `mask_text`'s internal string ops with a non-str argument
    (`planner.py:72`).
  - `capabilities`: `", ".join(str(c) for c in caps_raw) if isinstance(caps_raw, list) else
    ""` — non-list falls back to empty, non-str entries are cast (`planner.py:70-71`).
  - `read_blocks` `OSError`: `_warm_band_section` now catches it and degrades to "Evidence
    log unreadable." instead of propagating (`planner.py:119-125`).
  - Confirmed the cited sibling precedent still exists as described:
    `workspace_report.py` has the same `isinstance(..., dict)`/`isinstance(..., list)`
    pattern at lines 158/212/228/248/251/281 (not the exact 313/315 cited in wq#4520, but
    the same guard shape is present repeatedly); `agent_tui.py:85,114` match exactly.
- Ran `python3 -m pytest limpet/test_planner.py -q` → **25 passed** (9 original + 16 new =
  25 total, matching the claim).
- Ran `python3 -m pytest limpet/ -q --ignore=limpet/test_registry.py` → **759 passed**, not
  the claimed 751. Checked why: the worktree's history includes two commits that landed
  after wq#4509/wq#4520/wq#4528 in this lineage (`ce0fd9fb` wq#4526 kernel params,
  `7815c7ff` fail-closed stage4 RE tools) that are not part of this diff and plausibly added
  ~8 tests elsewhere in `limpet/` since wq#4528 was authored. This is unrelated test-count
  drift from other tickets, not a discrepancy in wq#4528's own test additions (confirmed
  separately as 25/25 above) — not treated as a contradiction.
- **Tried to break it** — ran a combined-worst-case probe deliberately drawn from *outside*
  the 16 new tests' individual-field population (each new test varies one field at a time;
  my probe corrupts `identity`, `hardware_bound` container and entries, HAL `capabilities`
  entries, and HAL `descriptor` simultaneously in one manifest/inventory pair):
  `identity=True`, `hardware_bound=[1, "x", None, {"adapter": 2}]`,
  `hardware.json` device with `capabilities=[None, True, {"x": 1}]` and
  `descriptor={"nested": "val"}`. `Planner(workspace=ws).build_system_prompt()` returned a
  degraded-but-non-raising prompt (`identity=(-)`, `hardware_bound=(?; ?; ?; 2)`,
  `capabilities=[None, True, {'x': 1}]`, `descriptor={'nested': 'val'}`) — no exception. This
  is a falsifiable probe outside the test suite's per-field population: if any guard were
  missing or miswired for a multi-field-bad-at-once case, this would have raised.
- Confirmed severity framing is unchanged from wq#4520/wq#4528: `grep -rn
  "build_system_prompt|Planner(" limpet/*.py` outside `planner.py`/`test_planner.py` → zero
  hits. Still not wired into a live agent loop.
- Checked the `pr_body_wq4528.md` file committed at the repo root alongside the fix: this is
  an established `lumen-pr` artifact convention (20+ prior commits across the repo's history
  commit a `pr_body_wqNNNN.md` at root the same way), not stray cruft left by this ticket.
- Checked ISSUE-LOG row 1523 (cited in wq#4528's notes, re: a mid-ticket `git checkout`
  unstaging the fix and shipping a test-only commit): documented with cause and recovery
  (`git add` + `commit --amend`), and the merged commit's content byte-matches the worktree
  fix, confirming the recovery actually landed the full diff, not the test-only one.

## Verdict

**CONFIRM.** Every shape wq#4520 and wq#4528's own audit identified as unguarded is now
guarded, verified against the actual code (not restated from the done-notes), with passing
tests I ran myself (25/25 `test_planner.py`, 759/759 non-registry `limpet/`, difference from
the claimed 751 explained as unrelated drift from other tickets' later commits). A
combined-shape probe outside the test suite's population did not find a gap. The fix is a
narrow, mechanical application of an already-proven pattern from two sibling modules in the
same codebase, exactly as judged. No new behavior is introduced beyond "degrade instead of
raise" — the module's docstring contract now holds for every JSON-representable shape of
`manifest.json` and `hardware.json`, not just the absent-source extreme the original wq#4509
tests covered.

One minor, non-blocking observation (not a contradiction): `descriptor = str(d.get(...) or
"")` treats a falsy-but-present descriptor (`0`, `False`, `""`) as absent, same as the
pre-existing `capabilities`/original code's `or` idiom — a cosmetic data-loss edge case, not
a crash, and consistent with the module's existing style elsewhere.
