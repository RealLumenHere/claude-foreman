# wq#2816 — verify-pass on wq#2758 [fleet-inv] YAML hardware block (ram/storage/cpu) is a second hand-kept copy

Verifier: claude-sonnet-5@high (fresh instance, same model as author). Author: claude-sonnet-5.
Target: `lumen/fleet-inventory` main, commit `751d218` (pushed directly — `lumen-pr` doesn't
operate on this repo, per ISSUE-LOG #281/#313 precedent). Citation doc `c5ec363` on foreman
branch `feat/wq2758-fleet-inv-yaml-hardware-block-ram-storag-a1` (ISSUE-LOG #691 workaround,
unmerged to foreman main by design — the acceptance gate only needs it to cite the real commit).
Verdict: **CONFIRM**.

## What was shipped

`fleet-inv drift` (751d218) adds two new per-machine comparisons to the existing
inventory-vs-state drift table: `hardware.ram.size_gb` (YAML) against the leading `NNGB` token
of the store's box-confirmed (wq#1688) `<node>.ram` free text, and `hardware.storage[].serial`
(YAML) against a case-insensitive substring check into the store's `<node>.storage` free text.
Each yields NO-FACT / UNVERIFIED / MATCH / DRIFT, folded into the existing Coverage/NO-FACT
ratchet exactly like the other identity fields. `tests/test_drift_coverage.py` gained 4 new
tests and updated the hardcoded per-scenario field counts (+2 fields x 2 stub machines).

## Adversarial checks run

- Read the full `fleet-inv` diff (`git -C /home/lumen/fleet/inventory show 751d218`) cold,
  including the new `_RAM_GB_RE` regex, the `cmd_drift` insertions, and the doc-comment.
- Checked `_clean()` (`fleet_inventory.py:458`) actually stringifies `size_gb` before the
  `yaml_ram == store_ram` comparison — confirmed both sides are `str`, so this isn't an
  int-vs-str false-DRIFT trap.
- Surveyed the real store's `<node>.ram` text for every reachable machine (`lumen`, `thor`,
  `hathor`, `isis`, `sobek`, `maat`, `khepri`, plus NO-FACT cases `apis`/`toilet`) — all
  non-empty values lead with `NNGB`, so `_RAM_GB_RE` (anchored `^\s*(\d+)\s*GB\b`) extracts
  cleanly; no real machine's free text broke the regex into a false MATCH or a crash.
- Wrote a standalone type-scan (`sys.path` import of `fleet_inventory`, not a shell loop) over
  all 25 `inv.all()` records checking `hardware.ram` is a dict and `hardware.storage` is a
  list of dicts everywhere — no schema outlier that could hit an `AttributeError`.
- Ran `pytest /home/lumen/fleet/inventory/tests/` — **59/59 pass**, matching the shipped notes.
- **Live re-run against the real store** (`fleet-inv drift --no-baseline -v`, read-only): counted
  MATCH rows per machine independently and got **exactly 9 machines with both fields matched**
  (thor, sobek, sia, maat, lumen, isis, hathor, bes, anubis) and **exactly 1 DRIFT** — `apep`
  `hardware.storage.serial` (YAML lists `S1F1NYAF429841,0xf5608fd7`; the store's stated text only
  contains the first serial) — reproducing the author's claimed numbers digit-for-digit without
  relying on their report.
- Confirmed `fleet-inv drift --no-baseline` exits **2** on the real fleet (the pre-existing apep
  drift), satisfying wq#2630 (an acceptance probe must be able to fail) with a live, unpinned-but-
  currently-true positive, backed by 2 of the 4 new unit tests pinning synthetic DRIFT cases as a
  reproducible fallback.
- Confirmed the citation doc (`c5ec363`, `docs/wq2758_yaml_hardware_block_drift_check.md`)
  exists and cites the real `751d218` commit, per the ISSUE-LOG #691 pattern for this
  dispatcher gap (fleet-inventory has no foreman `LANE_REPO` entry — a known, separately-tracked
  issue, not a defect in this diff).

## Things I tried to break, that held

- int/str RAM comparison — `_clean()` stringifies, no false DRIFT.
- Non-`NNGB`-leading ram text (e.g. `"2x16GB"`, decimals, MB units) — regex fails closed to
  NO-FACT, never a false MATCH; no real machine currently exercises this path but it's the
  correct conservative failure mode for a report-only, no-re-probe check.
- Predicate collision (`ram` vs a hypothetical `ram_ceiling`) — `_FACT_RE`'s predicate group
  is exact-matched as a dict key, not a prefix, so no collision risk.
- Schema variance across all 24 `machines/*.yaml` — none; every `hardware.ram`/`storage` is the
  expected dict/list shape.

No contradiction found. The diff does what the ticket and commit message claim, the numbers are
independently reproducible against live production data, and the new checks demonstrably surface
a real, previously-invisible drift. No escalation.
