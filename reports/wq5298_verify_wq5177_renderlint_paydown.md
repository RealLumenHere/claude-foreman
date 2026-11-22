# wq#5298 — verify wq#5177 (messenger renderlint baseline paydown 43 → 0)

Verdict: **CONFIRM**, plus one minor finding (a narrow detection loss in the new contrast clip-box skip)
that masks nothing on the shipped PWA today. Filed as a follow-up below, not a contradiction.

Subject: lumen-messenger PR #10, merged as `e8fa842` (on `origin/main`). Verifier: fresh instance.
Everything ran in a throwaway clone at `/tmp/fworker-wq5298/m`, checked out at `e8fa842`. The live
`/home/lumen/messenger` was not modified.

## What was claimed
- `tools/renderlint/baseline.json`: 43 accepted → 0
- the gate shows 0 errors
- 9 tests pass
- `rules.js` no longer scores lines clipped out of a scroller (ISSUE-LOG 1719)
- emoji icons replaced by an SVG `#i-*` sprite

## Checks run

| # | Check | Result |
|---|---|---|
| 1 | Ticket liveness probe: `len(baseline['accepted'])` in live `/home/lumen/messenger` | `0` (needs < 43) — PASS |
| 2 | `python3 -m pytest -q -p no:cacheprovider tools/renderlint` @ e8fa842 | `9 passed in 55.35s` — PASS |
| 3 | Full gate `python3 tools/renderlint/renderlint.py` (13 widths, stress on, app+login) | `0 error(s) (0 baselined, 0 NEW), 2 warning(s)` (tap-target warns only) — PASS |
| 4 | **Counterfactual:** shipped webapp gated with the PRE-change `rules.js` (`e8fa842^`) | also `0 error(s), 0 NEW`. The rules.js change hides no current webapp finding. The 43 → 0 is real paydown in `webapp/`, not the linter being loosened. |
| 5 | Can the new regression test fail? `test_contrast_skips_lines_clipped_out_of_view...` run against the pre-change rules.js | FAILS (`assert "#first" not in sels`). Under the new rules it passes, so the test does guard the fix. |
| 6 | Sprite integrity: every `#i-<name>` referenced in index.html / chat_manager.js / features_v2.js (`ico()`) / phase2_features.js (`LP_ico()`) has a `<symbol id="i-<name>">` | all 13 distinct names resolve against the 20 symbols. No dangling `<use>`, so no silently invisible icons (renderlint could not catch those). |
| 7 | Icon-only buttons keep an accessible name after the emoji were removed | `.msg-act-btn` buttons carry `aria-label` (phase2_features.js:513-517). The SVGs are `aria-hidden="true"`. |
| 8 | Service-worker cache bump so clients pick up the new assets | `sw.js` VERSION `lumen-v52-…` → `lumen-v53-20261122_a11y` |
| 9 | Commit trailer `Co-authored-by: Lumen` | Present on every recent messenger commit (lumen-pr tooling). Not added by the author. |

## Adversarial probe of the rules.js change

The probe file is `reports/wq5298_verify_wq5177_probe.py`. To run it, copy it into
`tools/renderlint/` of a messenger checkout pinned at `e8fa842`, then run
`python3 -m pytest -q -s tools/renderlint/<file>`.

The new `insideAll()` (rules.js:245-246) skips a text line from the contrast check unless the
line is **wholly** inside every clipping ancestor, on all four sides. The bug it fixes
(ISSUE-LOG 1719) is vertical only: a line scrolled under the fixed `#appbar`.

- **Ellipsis (no hole).** I tested a faint `#bbb` span ellipsized inside an `overflow:hidden; text-overflow:ellipsis` row. It is still flagged under the new rules. Chromium's range rects follow the truncation, so ellipsized labels such as `.qi-name`, `.ubar-name` and `.rc-file-n` stay covered.
- **Partly scrolled horizontal line (narrow hole, minor).** I tested a faint chip half past the right edge of a 150px `overflow-x:auto` bar, with the visible part painted at about 1.9:1. The pre-change rules flag `#cut`. The new rules do not (`[('contrast', '#whole')]` vs `[('contrast', '#cut'), ('contrast', '#whole')]`). `text-clipped` also stays quiet by design here, because the content can be reached by scrolling. The same applies to a single-line label partly clipped at the top or bottom edge of a scroller.
- **Impact today:** none. Check 4 shows the shipped webapp gives the same 0 errors under both rule versions.

**Follow-up suggestion (not a defect in the delivered scope):** clip the line rect to the
intersection of the clip boxes, and skip the line only when nothing usable is left (for example
`w < 4 || h < 6`). Do not skip every line that is partly clipped. That keeps the 1719 fix and
still scores the visible part of partly clipped lines.

## Conclusion
The 43 → 0 paydown is real. The fixes are in `webapp/`, and the rule change hides no current
finding (check 4). The new test can fail (check 5), the gate is green at every width with stress
on, and the icon sprite has no dangling references. **CONFIRM.**
