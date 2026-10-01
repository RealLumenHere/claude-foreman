# wq#3294 — Commander HA artifacts

See `DESIGN.md` for the architecture (items A–E) and the failure matrix.

| File | What |
|---|---|
| `DESIGN.md` | Zero-downtime control-plane design; which item ships where. |
| `pool_failover.py` | Item B: health/failover loop. Probes each backend with POST /mcp `tools/list` (wq#2606's proven probe), hysteresis (`fail_threshold`/`rise_threshold`), persisted state, configurable demote/promote hooks. Stdlib-only, `--once` for timer mode. |
| `commander-pool-failover.service` | Bes-homed unit for the loop (runs on Bes, not Lumen — must survive Lumen's death). |
| `mcp_probe.py` | Full MCP e2e (initialize + tools/list) against any streamable-HTTP URL; exit 0/1. |
| `dc_deploy_canary.sh` | Item D: installs candidate supergateway+DC versions into a scratch prefix, serves them on a throwaway port, gates on `mcp_probe.py`. Upgrade scripts must abort unless it exits 0. |
| `test_pool_failover.py` | 14 tests: probe against real local HTTP servers (JSON, SSE, error-body-200, HTTP 500, dead port), hysteresis/no-flap, hook firing exactly once, state surviving restart, `--once` mode. `python3 -m pytest test_pool_failover.py` from this dir. |

Deployment is follow-up ticket 4 (DESIGN.md §follow-ups): install the loop on Bes,
wire the canary into every pool backend's DC upgrade path. Items A (Worker
per-request failover), C (Bes live + P1 Pro hardware), E (OAuth state → KV) are
follow-ups 1–3 — they need the mcp-oauth-worker repo, ssh, or a purchase this lane
cannot perform.
