# wq#3471: probe-dead-references false positives (tbl_sweep2.py)

`probe-dead-references` fired from 2026-09-16 08:18 PDT on (21 pages). Every line it
reported was a checker bug. `/home/lumen/sentinel/probes/sentinel_probes.py:248` runs
`/home/lumen/audits/tools/tbl_sweep2.py`, and the sweep had three false-positive classes:

| flagged | cause | fix |
|---|---|---|
| `lumen_dream_digest.py` `note_merges`, `prose_demotions` (x5) | `DB = f'{MINERVA}/minerva.db'`: `db_pat` only matched a path right after a quote, so it never saw minerva.db and checked those tables against queue.db. wq#1880 diagnosed this and proposed the fix, but it never shipped (fire-fence block). | `db_pat` also matches right after an f-string `}` |
| `minerva_audit.py:48` `ON`, `lumen_state.py:282` `OF` | trigger grammar: `BEFORE UPDATE ON t`, `AFTER UPDATE OF col ON t` | `on`/`of` added to `SKIP` (reserved words, never an unquoted table name) |
| `lumen_state.py:254` `that`, `lumen_migrate.py:2` `calls` | docstring prose ("an UPDATE that sets...") | strings that start a module/class/function docstring are skipped (`ast`) |

Over the live scan roots this removes exactly those 9 lines and adds none (35 -> 26 raw
lines; the probe's own filters handle the other 26). Once the f-string path resolved,
`note_merges`/`prose_demotions` were found in `/home/lumen/minerva/minerva.db`. That is
the direct check wq#1880 could not make.

`tbl_sweep2.py` now takes optional scan roots as argv so tests can run against fixtures.
With no args it scans the same four default roots as before.

## Install / verify

```
ops/wq3471-tblsweep-false-positives/install.sh            # backup + atomic install
ops/wq3471-tblsweep-false-positives/install.sh --verify   # byte-match the live copy
python3 -m pytest -q ops/wq3471-tblsweep-false-positives/tests
```

Deployed 2026-10-07 (backup `tbl_sweep2.py.bak.20261007T080411Z_pre_wq3471`). The forced
probe run printed `PASS probe-dead-references` and `EDGE-CLEAR ... resolved 2 stale aq
row(s)`, and `_fires` no longer contains it.
