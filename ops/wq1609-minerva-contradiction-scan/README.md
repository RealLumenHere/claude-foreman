# wq#1609 — contradiction scanner beyond IP-shaped values

## Root gap

`lumen_dream.superseded_ips()` keeps a closed fact only when
`IP_RE.fullmatch(value)` succeeds. Of **316 closed facts, 6 are IP-shaped**, so
the only staleness detector the fleet owns is blind to `status` (54),
`power_plug` (15), `role` (10), `powers_node` (6), `lan_ip` (4) and everything
else. The 2026-09-06 dream report printed "Stale prose flagged (0)" while
`thor.fan_status = dead` (superseded 2026-07-13) sat live in a machine-retrieved
note and `khepri.power_plug = P1` (superseded 2026-08-14) sat live in
`FLEET_MAP.md`. Ref `research/MINERVA_STALENESS_REVIEW.md` §2.4, §3.5, §2.5.

## What this ships

`lumen_contradiction_scan.py` → `/home/lumen/bin/lumen-contradiction-scan`,
driven daily by `lumen-contradiction-scan.timer` (06:20 UTC) instead of weekly
inside `lumen-dream` (§2.5: a fact superseded on Monday waited six days).

**Dead-value index** over *every* closed fact, not the IP-shaped 6. A closed
value enters the index when it is ≤60 chars, matches `^[A-Za-z0-9._:/@-]+$`, is
not `fact_class=narrative`, is not a volatile predicate (`price`, `actual_load`,
`pr_count` — a doc quoting last month's eBay price is not fleet staleness), and
is not still present inside the current value (`power_plug` closed `P3` and
reopened as `P3 (relabelled …)` is an annotation, not a supersession).

**Matching is by value shape, never substring-over-prose** (§3.5):

| value | example | match rule |
|---|---|---|
| self-identifying | `100.88.71.47`, `lumen-brain.service`, `embeddings-host`, `DECOMMISSIONED` | matched anywhere — the token carries its own subject |
| bare label / common word | `active`, `dead`, `P3`, `E1` | matched only where the fact's **subject** *and* a **predicate cue** are in scope |
| prose | anything >60 chars or `fact_class=narrative` | **never string-matched** |

The predicate cue is what keeps the bare labels honest: `thor … dead` is only a
`fan_status` claim when `fan` or `status` is in scope. Without it the live store
produced 22 hits, nearly all of them notes that merely contained the word
`dead` somewhere; with it, 3. Word boundaries respect the value alphabet, so
`100.88.71.47` does not match inside `100.88.71.470` and `active` does not match
inside `inactive`, while a sentence-final `100.88.71.47.` and a YAML `hathor:`
still do.

Scope of "in scope": a prose line matches on its own line, with the subject
allowed to come from its enclosing YAML block key or nearest markdown heading; a
note matches line by line, with the subject allowed to come from the note title
or ±2 lines. Whole-note matching was the first implementation and it was noise —
a 60-line note that says `thor` in its header and `dead` forty lines later is
not asserting `thor.fan_status = dead`.

**Suppressors**, all three on every candidate hit:

- *negation* — `was`, `no longer`, `retired`, `superseded by`, `stale`, `dead`,
  `do not use` … A negation word that *is* the dead value does not count, or
  `fan_status = dead` could never be reported at all.
- *changelog* (new) — the current value appears on the same line, or anywhere in
  the same note → the text documents the transition. Prose current values are
  matched through their self-identifying and shouted tokens (`WORKING`,
  `2026-07-16`), since the sentence as a whole is not matchable.
- *as-of* (new) — the line/note carries a date earlier than the fact's
  `valid_to` → correct historical record → severity drops to `info`, not
  suppressed.

**Artifact reach and action tier** (§3.5 table), `--apply` performs only the
machine tiers:

| artifact | tier | `--apply` does |
|---|---|---|
| `archival_notes` | `note_row` | insert `note_contradictions` row (table self-migrates) |
| `LIVE_FLEET_STATE*.md` | `regenerate` | run `/home/lumen/bin/state-regen` |
| `storage_manifest.yaml`, `fleet/inventory/machines/*.yaml`, `retired_services.yaml` | `ticket` | `wq add` (P3, build) + `aq` row |
| `FLEET_MAP.md` | `proposal` | report only |
| `claude_memory.md`, `instincts.yaml` | `proposal_only` | **never written, under any flag** |

## Measured reach

`python3 measure_reach.py` against the live store, 2026-09-10:

```
closed facts: 316
old IP-only filter reach: 6
  184  skipped: prose value over 60 chars
   62  skipped: volatile predicate
   27  skipped: fact_class=narrative
   18  skipped: multi-token value
   17  INDEXED
    8  skipped: value still live in the current value
```

**17 of 316, not the 200+ §3.5 estimated.** The estimate assumed closed values
are structured tokens; 184 of them are prose sentences (`root_disk` =
`"227G/466G 51% used, 220G free after 2026-08-09 offload (was 365G/82%)"`), and
§3.5 forbids string-matching those for good reason. The remaining coverage lever
is upstream — facts written as prose blobs instead of a value plus a note —
filed as wq#1629, not paperable here.

Reach that matters is hits, not index size: the weekly dream pass reported **0**;
this pass reports **3 actionable** on the same store, each one a real superseded
value in live text:

```
action note_row     archival_notes:ac50dd3d7452  thor.fan_status = 'dead'
                    "likely Thor thermal throttle (dead fan)"  (closed 2026-07-13, now WORKING)
action note_row     archival_notes:719d13a1483a  gameboy-pc.power_plug = 'E1'
action proposal     /home/lumen/FLEET_MAP.md:271 khepri.power_plug = 'P1'
                    "Plug->node: p2 nephthys/p3 hathor/…"      (closed 2026-08-14, now NONE)
```

## Usage

```
lumen-contradiction-scan                 # read-only report (default)
lumen-contradiction-scan --json          # same, machine-readable
lumen-contradiction-scan --apply         # + machine tiers (what the timer runs)
lumen-contradiction-scan --no-files      # archival_notes only
lumen-contradiction-scan --strict        # exit 1 when actionable hits exist
lumen-contradiction-scan --selftest      # run the test file
```

Exit 0 = ran clean, 1 = actionable hits (`--strict` only, so the daily timer
does not flap red on a finding it already reported), 2 = the scan broke.

## Install

```
ops/wq1609-minerva-contradiction-scan/install.sh
```

Runs the tests, installs the script and both units, enables the timer, then
prints one live report.

**Deploy step 2 — remove the weekly duplicate.** `lumen_dream.py` still calls
`stale_prose_scan()`/`superseded_ips()` on the Sunday run. Once the daily timer
is up, delete that section from the dream pass (`lumen-dream.service` keeps
dedup/instincts/salience). This worktree cannot make that edit —
`/home/lumen/minerva/` is outside the build worker's allowed directories, so the
file is neither readable nor writable from here; it is a two-line deletion in a
file no test covers.

## Tests

`python3 -m pytest -q test_lumen_contradiction_scan.py` — 24 tests, all green:
index selection and exclusions, boundary matching, subject scoping and block
context, predicate cues, all three suppressors, artifact tiering, note
line-scoping and revoked/superseded exclusion, dry-run read-only-ness, idempotent
`--apply`, CLI exit codes. Verified additionally against a `.backup` copy of the
live store: 2 `note_contradictions` rows on the first `--apply`, 0 on the second.
