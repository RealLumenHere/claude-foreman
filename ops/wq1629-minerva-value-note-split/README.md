# wq#1629 — a fact's value is a token, its prose is a note

`lumen_state.py ingest` stored the whole sentence in the column a scanner reads:

    root_disk = '227G/466G 51% used, 220G free after 2026-08-09 offload (was 365G/82%)'

`MINERVA_STALENESS_REVIEW.md` §3.5 estimated a generalised contradiction scanner
would reach 200+ of 312 closed facts; wq#1609 built it and reached 17. The gap was
never in the scanner — §3.5 forbids string-matching prose, and the store was
writing prose into `value`. **The coverage ceiling was set at ingest.**

Measured on minerva.db, 2026-09-11 (364 closed facts, `measure_reach.py`):

| | indexed | scan hits |
|---|---|---|
| before | 22 | 14 |
| after (status/root_disk/availability) | **64** | **32** |
| after (`-p ALL`, measured on a copy) | 136 | — |

## What landed

### The splitter — `state_value_split.py` (this directory, deployed to `/home/lumen/minerva/`)

`split_value(predicate, value, note=None) -> (value, note, rule)`. Deterministic,
no model, and it refuses to guess: no rule matches, nothing is split, the value
stays prose. Rules, in order:

- `state_anchored` — the shouted state word at the front: `DEAD confirmed
  2026-08-13 — no power` → `DEAD`. Up to three caps words, joined (`IN SERVICE`
  → `IN-SERVICE`).
- `state_vocabulary` — a state word that is not first, from a closed vocabulary:
  `tailnet OFFLINE since 2026-07-26` → `OFFLINE`. Closed on purpose — any
  all-caps word would also catch `AIO`, `NOT` and `PT`.
- `measure_size_pair` — `80% used (357G/466G), 90G free` → `357G/466G` (`%` is
  not in the scanner's token alphabet, the size pair is).
- `prefix_token` — any predicate: a token, a prose separator, then prose.
  `P3 (relabelled 2026-08-11)` → `P3`.

A token taken off the front leaves the remainder as the note; a token found
mid-sentence keeps the whole original as the note, because cutting it out would
mangle the sentence. Either way nothing is lost: `value_display` = value + note.

### Ingest — `/home/lumen/minerva/lumen_state.py` (live edit, not a repo file)

- `note` column, added by `_migrate` (`lumen_state.py:223-228`).
- `ingest(..., note=None)` / CLI `--note` — give the prose yourself and your
  value is stored verbatim (`lumen_state.py:473-486`, `:805-809`).
- The split runs after the wq#1700 gate and before classification, so a refused
  write splits nothing, `fact_class` sees the token, and supersession compares
  token to token (`lumen_state.py:553-560`). `lifecycle` is a closed enum and is
  never split.
- Consequence worth the ticket on its own: re-observing `OFFLINE since 46d` as
  `OFFLINE since 47d` is now a **reconfirm**, not a supersession — the state did
  not change, only the prose did. The note is refreshed; a bare-token write keeps
  the stored note rather than erasing the story (`lumen_state.py:580-586`).
- `current()` carries `note` and `value_display`; `_classify_fact` takes the note
  so `render_tier` measures what actually renders (`lumen_state.py:157-182`,
  `:330-341`, `:741-742`).
- Renderers show `value_display`, so splitting a fact does not delete the
  sentence from the projections: `lumen_state_gen.py:50-53`,
  `lumen_proposal_digest.py:150-154`.

### Backfill — `lumen-state-split backfill [--apply] [-p PRED]`

Rewrites `value`/`note` in place for facts already stored. Dry-run by default.

- Windows are never touched — a split is the same fact re-shaped, so `asof`
  answers identically afterwards.
- `fact_class` **is** recomputed (it was `narrative` because the value was a
  sentence; that is one of the scanner's own skip reasons). `render_tier` is
  **not**: the split renders the same characters, so it must not move a fact into
  the injected core block. Measured the hard way — recomputing the tier from the
  token alone took the core block from 5375 to 8703 tokens against a 4000 cap.
  After the fix: 5385 vs 5375 pre-backfill.
- Every rewritten row gets a `memory_audit` row; the projections are marked dirty
  (wq#1633).

### Scanner — `../wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`

`self_identifying()` treated any uppercase word of 6+ letters as carrying its own
subject. That was harmless only while status values were prose the index could
not read; once this ticket turned them into tokens it put words like `RETIRED`
into the index unscoped, and the live scan went **14 → 777 hits**. Shouting does
not identify a subject, so those values are now matched subject-scoped like their
lowercase twins: 32 hits at 64 indexed values, and the pre-split baseline is
unchanged at 14 — the tightening loses no existing find.

## Evidence

    python3 -m pytest -q ops/wq1629-minerva-value-note-split      # 39 passed
    python3 -m pytest -q ops/wq1609-minerva-contradiction-scan    # 25 passed
    python3 ops/wq1609-minerva-contradiction-scan/measure_reach.py

Live run, 2026-09-11: backup `→ /home/lumen/minerva/minerva.db.bak.wq1629`,
`backfill --apply` split 99 rows (17 left as prose), `lumen_state.py check` = OK
(0 violations), reach 22 → 64.

## Still open

- 176 closed values are still prose no rule splits (`lumen-state-split report`).
  They are genuinely prose (`role = 'RE/forensics bench (ASRock N3450)'`), and
  §3.5's answer for those is an episode with a short fact pointing at it, not a
  cleverer regex.
- `-p ALL` reaches 136 of 364 (measured on a copy). Not applied: this ticket
  scopes the backfill to status/root_disk/availability.
- The projections are marked dirty but not regenerated — `state-regen` is not on
  the build worker's command allowlist. Run it, or let the next regen pick the
  flag up.
- wq#1609 is not installed on Lumen (`/home/lumen/bin/lumen-contradiction-scan`
  absent, no timer), so the scanner fix above ships as code, not as a live change.

## Why a live edit, not committed source

`/home/lumen/minerva/**` is not in this repo (same as wq#1689/wq#1613). The
module edits are cited by `file:line` above and pinned by tests that import the
real `/home/lumen/minerva/lumen_state.py`, so a drifted deployment fails the
suite rather than passing quietly.
