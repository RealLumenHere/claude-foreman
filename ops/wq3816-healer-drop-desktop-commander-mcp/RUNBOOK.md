# wq#3816 — drop masked desktop-commander-mcp from the healer watch table

Live file: `/home/lumen/agent/healer_watch_table.yaml` (outside git; the healer reloads it on mtime
change, not in this BUILD worker's allowed dirs per `harness/fleet-exec-policy.yaml:66-69` —
deliberately excluded, live `.env`/`.env.bak` tokens). `healer_watch_table.diff` here is the
planned record of the edit; `token-engine/check_deploy_drift.py` applies `install.sh` to the
live target after this PR lands, same path as wq#1892 and wq#3575.

- Why: `systemctl show desktop-commander-mcp -p LoadState` → `masked` (3 consecutive successful
  reads, per ticket body); retirement recorded in `docs/fleet/retired_services.yaml:319-331`
  (wq#3805 retire, wq#3781 route removal) — Commander now runs on Horus (mcp-proxy) via the
  `mcpw.lumennode.uk` Cloudflare Worker, nothing on Lumen connects to the old `:3100` route.
  The wq#1112 stale-target guard skips the row and files a ticket every cycle, which is what
  auto-filed this one (same shape as sentinel's wq#1092).
- Deploy (as lumen, no root): `bash ops/wq3816-healer-drop-desktop-commander-mcp/install.sh`.
  Removes the row, backs up to `healer_watch_table.yaml.pre-wq3816`. Idempotent.
- Live check: `install.sh --verify` — row absent and table still parses to a non-empty
  `units` map.
- Not touched: `desktop-commander-mcp` guard scripts (`commander-blocklist.sh`,
  `dc-redeploy.sh`, etc. in lumen-bin, PR #251) that already exit 0 when masked — this
  ticket only concerns the healer's watch table, not those guards.
- Revert: re-add the removed row from `healer_watch_table.diff`.
