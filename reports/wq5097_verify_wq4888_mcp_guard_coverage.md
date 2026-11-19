# wq#5097 verify-pass: wq#4888 (lumen_mcp_guard on the 6 unguarded MCP servers)

Verdict: **CONFIRMED.** Verifier: orch-verify-batch-5099 (claude-opus-5-5), 2026-11-18 22:10 PST.
Subject: PR #2639 (b0d1d9ba) plus the follow-on pins work (6e51c137, 639294d4).

## What was checked, on the live box

| Check | Result |
|---|---|
| `~/.claude.json`: docker, playwright, stealth-browser, gitea, grafana, council each launch a `launch.sh` that execs `_guard/lumen_mcp_guard.py` | 6/6 guarded (`grep -c lumen_mcp_guard` = 2 per launcher) |
| Live guard sha256 == `ops/wq4514-mcp-install-batch/lumen_mcp_guard.py` on main | 3f40a696b28b == 3f40a696b28b |
| `bash ops/wq4514-mcp-install-batch/install.sh --verify` (the bundle's --verify handler exists, line 10) | `VERIFY OK` |
| Unit: `test_lumen_mcp_guard.py test_install_launchers.py` in the guard venv | 54 passed, 32 subtests |
| Live smokes in the guard venv (stealth_browser, docker, grafana, playwright, fleet_state, gitea `--skip-issue`, council `--no-spend`) | all PASS |

Adversarial probes that the smokes ran and that held:
- docker: `remove_volume`, `create_network`, `remove_network` → `Unknown tool`; `docker ps` unchanged (9 → 9).
- gitea: `delete_file`, `create_or_update_file`, `create_repo`, `actions_config_write` → `Unknown tool`.
- grafana: annotation delete and alerting writes → `Unknown tool`; dashboards/folders unchanged.
- playwright: `browser_file_upload`, `browser_drop` refused; canary servers saw zero requests; no leaked Chrome.
- stealth-browser: `spawn_browser sandbox=False` guard-denied; page content arrives inside untrusted markers.
- council: exactly 5 tools listed; `ask_deepseek model=anthropic/...` and `model=lumen/...` guard-denied.

## Findings that do not contradict 4888 (filed as follow-ups)

1. **stealth-browser and council launchers have no `--pins`.** The other four guarded launchers
   carry the wq#4941 tool-description pins (wq#4958); these two were added by 4888 after the pins
   pass and were never pinned. 4888's scope was guarding, which is met.
2. **`serial-maksoor` is a raw HTTP MCP** (`http://100.81.204.74:8400/mcp`) in `~/.claude.json`, outside
   4888's list of six. Hardware-facing, so action_gate covers it at call time, but there is no
   guard-level allowlist.
3. **Test trap, not a defect:** in a Commander shell `PYTHONPATH=/home/lumen/opt/mcp-proxy/lib`, so the
   guard unit suite shows 37 failures (`pydantic_core._pydantic_core` not found) unless run with
   `env -u PYTHONPATH`. The RUNBOOK command does not unset it.
