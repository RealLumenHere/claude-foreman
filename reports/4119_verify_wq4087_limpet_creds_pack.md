# wq#4119 verify of wq#4087 — Limpet creds pack (PR #2042)

**Verdict: CONFIRM.** No defects found. Author's claimed verification reproduced
independently.

## What was checked

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2042, merged to main as
`2ba265e139` (branch `feat/wq4087-limpet-creds-pack-ship-defaultcreds-many-a1`,
commits `10454c07` → `f75bb27f` → `2ba265e1`). Diff: +7218 lines across
`limpet/DESIGN-DECISIONS.md`, `limpet/kb-sources.d/many-passwords.toml`,
`limpet/kb/creds/{DefaultCreds-Cheat-Sheet,ics-default-passwords,many-passwords}.{csv,LICENSE}`,
`limpet/kb/creds/SOURCES.md`, `packaging/limpet-tools/debian/{changelog,rules}`.
Tree is additive-only; confirmed `git diff main 2ba265e1` shows zero delta for every
file this ticket touches (main had already absorbed the identical content as
`24fb54a0`, a separately-hashed squash of the same tree).

### 1. CSV integrity (independent re-parse, not trusting the PR's own counts)
`csv.DictReader` over all three files: zero field-count mismatches, and exact
row-count match against `SOURCES.md`'s table:
- `DefaultCreds-Cheat-Sheet.csv`: header `productvendor,username,password` (matches
  `kb-sources.d/defaultcreds.toml`'s declared fields exactly), 3767 data rows.
- `ics-default-passwords.csv`: header
  `vendor,product,username,password,protocol,port,device_type,source`, 107 data rows.
  `kb-sources.d/ics-default-passwords.toml`'s 5 declared body_fields are all present
  by name (`kb.py`'s `_row_to_entry.join()` looks up by header name via `row.get(f)`,
  so the 3 extra columns — `port,device_type,source` — are harmless, just unused by
  `title_fields`/`body_fields`). Note: `SOURCES.md` describes the reshape as landing
  "the TOML's declared header" (5 cols) but the shipped file keeps 8 — a minor
  documentation imprecision, not a functional bug (verified via `kb.py` source read,
  not just the loader not crashing).
- `many-passwords.csv`: header
  `Vendor,Model/Software name,Version,Access Type,Username,Password,Privileges,Notes`,
  2860 data rows. All 6 of the TOML's `body_fields` present by exact name/case.

11 `ics-default-passwords.csv` rows have an empty `username` with a real `password`
(e.g. Emerson UL33 UPS: password `123456`, no username) — checked these are
genuine upstream "password-only" device defaults, not evidence the claimed
"split `Default password` on the first `:`" transform mis-split anything; a row
with an embedded second `:` in the password (Beck IPC `IPC@CHIP`: username
`PPPSERVER`, password `, ppps:ppps`) shows the split correctly took only the
*first* colon and left the rest in the password field.

### 2. License files match claimed provenance
`DefaultCreds-Cheat-Sheet.LICENSE` → MIT, Copyright (c) 2022 Ihebski (matches
claimed upstream `ihebski/DefaultCreds-cheat-sheet`).
`ics-default-passwords.LICENSE` → MIT, Copyright (c) 2018 Daniel Miessler (matches
claimed upstream `danielmiessler/SecLists`, used instead of the TOML-named
`arnaudsoullie/ics-default-passwords` per DESIGN-DECISIONS.md D2 — that repo really
is a thin unlicensed 6-row stub, checked by the author, not independently re-checked
here since it requires a live fetch and the claim is checkable from the shipped
LICENSE's own attribution).
`many-passwords.LICENSE` → verbatim GNU GPL v2 text (339 lines, correct FSF
boilerplate), matches `[source] license = "GPL-2.0"` in the new TOML.

### 3. End-to-end re-run (not trusting the done-notes claim)
Ran `limpet/kb.py update` against the real `limpet/kb` + `limpet/kb-sources.d` on
disk, fresh `--db`:
```
indexed defaultcreds: 3767 rows from 1 file(s)
indexed ics-default-passwords: 107 rows from 1 file(s)
indexed many-passwords: 2860 rows from 1 file(s)
```
Exact match to the done-notes' claimed "3767/107/2860". Then
`limpet-kb search <query> --category creds` (plain and `--json`) returns real hits
spanning all three new sources (`creds/defaultcreds`, `creds/ics-default-passwords`,
`creds/many-passwords`), confirming the category is genuinely queryable end-to-end,
not just indexed.

### 4. Regression check
`python3 -m pytest limpet/test_limpet_kb.py` (run from `/tmp`, outside `limpet/`, to
dodge the pre-existing `limpet/registry/` vs `limpet/registry.py` shadow import bug
already logged as ISSUE-LOG row 1372 by this ticket's author): 21 passed, 0 failed.

### 5. Packaging
`debian/rules` `override_dh_auto_install` adds `cp -r $(SRC)/kb/creds
$(PKGROOT)/usr/share/limpet/kb/` after the wq#4086 `install -d .../kb`, landing all
7 new files (3 CSV + 3 LICENSE + SOURCES.md) at the path the TOMLs' `path =
"creds/*.csv"` entries expect, relative to the `kb-root`. `debian/changelog` bumps
`0.1.1` → `0.1.2` with an accurate summary; date `Sat, 17 Oct 2026` matches the
actual merge day.

### 6. Dead-end / abandoned-draft check
DESIGN-DECISIONS.md D1 documents an earlier draft that built a standalone
`limpet/creds.py` + `limpet/cli.py` `creds` subcommand, dropped in favor of
wq#4086's generic `limpet-kb search --category creds`. Confirmed no orphaned
remnant: `limpet/creds.py` does not exist on disk, `limpet/cli.py` has zero
`creds` references, no `test_limpet_creds.py` file anywhere in the tree. (An
earlier worktree-snapshot copy of `evidence.json`, taken 18s after the fixup
commit deleted `creds.py`, still cited `limpet/creds.py:95-114` for AC1 — but the
*tracked* `_runs/wq4087/evidence.json` that the acceptance gate actually checks
against has the corrected citation `limpet/kb-sources.d/many-passwords.toml:5-17`,
which is real and in-diff. The stale snapshot is an artifact of the snapshot
mechanism's timing, not a gate miss — checked both files before treating this as
a finding.)

### 7. No merge-conflict markers
Grepped all new/touched files for `<<<<<<<`/`=======`/`>>>>>>>` — none found,
despite the branch having a mid-stream `Merge remote-tracking branch 'origin/main'`
commit (`f75bb27f`).

## Conclusion
Every claim in the PR description and `wq done` notes was independently
reproducible: row counts, license provenance, search functionality, packaging
paths, and the ISSUE-LOG cross-reference (row 1372) all check out. No contradiction
found; no escalation to opus-5@high needed.
