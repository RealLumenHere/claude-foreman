# wq#1892 — drop masked lumen-pokemon-live from the healer watch table

Live file: `/home/lumen/agent/healer_watch_table.yaml` (outside git; the healer reloads it on mtime change).
`healer_watch_table.diff` here is the record of the edit already applied to it on 2026-09-20.

- Why: `systemctl show lumen-pokemon-live -p LoadState` → `masked`; retirement recorded in
  `docs/fleet/retired_services.yaml:200-215` (wq#2073). The wq#1112 stale-target guard skips the row and
  files a ticket every cycle, which is what auto-filed wq#1892.
- Verified: table parses (`yaml.safe_load`), 11 units remain, `lumen-pokemon-live` absent.
- Not touched: `lumen-pokemon-live` in `LUMEN_PUBLIC_RUNBOOK.md` / sentinel probes (wq#1895/wq#1898 lane).
- Revert: re-add the removed row from the diff. Do NOT unmask the unit without reading the wq#2073 RCA.
