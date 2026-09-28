# wq#2889 — verify of wq#2878 (PR #1207): CONFIRMED

Adversarial verify-pass (archetype 8), claude-sonnet-5@high, fresh instance. Scope: the
predicate-class floor + corrected N>=1 census in `build_dead_value_index`
(`ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`, commit `01ccd41`,
diff against parent `01ccd41^`). Did not re-implement; tried to break the diff and its
own falsifiable-evidence table.

## Probes

| check | result |
|---|---|
| `pytest ops/wq1609-minerva-contradiction-scan/test_lumen_contradiction_scan.py` | 75 passed (report's claimed count) |
| independent probe (own fixture, own baseline extraction via `git show 01ccd41^:...`, not the author's test code) — `/tmp/fworker-wq2889/probe_verify.py` | N=1 sharer: baseline scoped=False (bug reproduces), treatment scoped=True (fixed). N=0 sharers (post-bump steady state): baseline scoped=False, treatment scoped=True. N=2 sharers: both scoped=True. Unique IP under a non-shared predicate: both stay scoped=False. All 4 match the report's table |
| `SHARED_VALUE_PREDICATE_RE` sweep against every predicate in the live `state_facts` table (`kernel%`, `os%`, `distro%`, `bios%`, `firmware%`, `model%`, `%version%`) restricted to **closed, structured, self-identifying** values — the only rows where the predicate-floor can actually change the outcome | 3 rows match, all `kernel` (`nephthys`/`sobek`/`thor`, the intended wq#2850/wq#2812 class). No other predicate (`bios_prereqs`, `firmware_kit`, `model_confirmed`, `model_fit`, `firmware_restore_mode`, etc.) reaches this path — their values are long prose, excluded by `is_structured` before `scoped` is ever computed |
| `bash ops/wq1609-minerva-contradiction-scan/install.sh --verify` | exit 1, `MISS differs: /home/lumen/bin/lumen-contradiction-scan`. Not a new finding — the wq#2878 report already disclosed this (`install.sh` never re-run since PR#1188) and filed it as wq#2883, ops lane. This verify-pass is of the diff, which is unaffected by deploy status |

## Ruling re-checked, not just re-run

The wq#2878 report's central claim is that PR#1188 (wq#2850) satisfied item (1)'s `>=2`
threshold but failed item (2) ("never flag lines that do not name the closed subject" for
*any* other open subject, i.e. N>=1) outright. I read wq#2850's original two items
independently before reading the report's argument, and read the same way: item (2) is
not gated on item (1)'s derived set in the ticket text, so a straight `>=2` census does
leave wq#2812's own N=1 incident (one other node on `-30`) unfixed. The judgment's ruling
(NOT WAI) and the diff's two-part shape (per-value-minus-self census for the general case,
a predicate-name floor for the fleet-vocabulary classes the ticket names) both hold up.

## Non-blocking observations

- `SHARED_VALUE_PREDICATE_RE` matches on predicate **name shape**, not on whether the
  value is actually shared vocabulary: `bios_serial`, `firmware_uuid`, `model_serial`,
  `os_install_id` all match the pattern (verified directly against the compiled regex),
  and any of those would be forced `scoped=True` regardless of census if they ever held a
  short, self-identifying value that happened to be a genuine per-subject identifier
  rather than vocabulary. This does not currently misfire — no such predicate holds a
  short/structured/self-identifying closed value in the live store today (checked above)
  — but it is a latent risk if a `*_serial`/`*_uuid`-style predicate is added under one of
  the six stems later. Worth a follow-up narrowing (e.g. exclude `_serial`/`_uuid`/`_id`
  suffixes from the floor) if/when such a predicate appears; not a reason to contradict
  this diff against the ticket as scoped.
- The fix is not live (see probe table above); already tracked on wq#2883, not duplicated
  here.

## Verdict

CONFIRMED. No contradiction. wq#2878's diff matches its own judgment's ruling, the
regression tests reproduce independently, and the predicate-floor's only live-data
exposure today is the intended kernel-build class.
