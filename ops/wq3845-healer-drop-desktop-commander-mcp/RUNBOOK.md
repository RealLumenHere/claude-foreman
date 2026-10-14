# wq#3845 — drop masked desktop-commander-mcp from the healer watch table

Live file: `/home/lumen/agent/healer_watch_table.yaml` (outside git; the healer reloads it on
mtime change, and outside this BUILD worker's allowed dirs). `healer_watch_table.diff` here is
the record of the intended edit; `install.sh` is the idempotent script that applies it.

- Why: `systemctl show desktop-commander-mcp -p LoadState` → `masked` (confirmed live, this
  ticket). Retirement recorded in `docs/fleet/retired_services.yaml:319-331` (wq#3805 retire,
  wq#3781 route removal) — Commander now runs on Horus (mcp-proxy) via the
  `mcpw.lumennode.uk` Cloudflare Worker; nothing on Lumen connects to the old `:3100` route.
  The wq#1112 stale-target guard skips the row and files a ticket every cycle, which is what
  auto-filed this one (same shape as sentinel's wq#1092).
- Duplicate note: wq#3816 shipped an identical bundle
  (`ops/wq3816-healer-drop-desktop-commander-mcp/`) on branch
  `feat/wq3816-healer-watch-table-targets-retired-unit--a2`, pushed but not yet merged to
  `main` as of this ticket. Because that fix never landed, the live table still carries the
  row, so the guard re-filed this ticket (wq#3845). This bundle is independent and lands the
  same edit; whichever PR merges first makes the other's `install.sh` a no-op (idempotent —
  `grep` finds nothing, prints "unchanged").
- Deploy (as lumen, no root): `bash ops/wq3845-healer-drop-desktop-commander-mcp/install.sh`.
  Removes the row, backs up to `healer_watch_table.yaml.pre-wq3845`. Idempotent. Per
  `ops/VERIFY_CONTRACT.md`, merged is not live — this is a privileged/non-worker step; a
  sandboxed BUILD worker cannot run `bash ops/*` directly (not on the worker-settings.json
  allowlist) and the live file is outside its allowed dirs regardless.
- Live check: `install.sh --verify` (contract: `ops/_verify.sh`) — row absent and table still
  parses to a non-empty `units` map. Exercised read-only against a temp file in
  `ops/tests/test_wq3845_verify.py` (the live table is never touched by this worker).
- Not touched: `desktop-commander-mcp` guard scripts (`commander-blocklist.sh`,
  `dc-redeploy.sh`, `dc-narrow-deploy.sh`, `dc-narrow-restart-v2.sh`, `mcp-pressure-guard.sh`,
  `mcpw-stateful-cutover.sh` in lumen-bin, PR #251) that already exit 0 on masked — those are
  out of scope for this row removal.
- Revert: re-add the removed row from `healer_watch_table.diff`. Do not unmask the unit
  without reading the wq#3805/wq#3781 retirement record first.
