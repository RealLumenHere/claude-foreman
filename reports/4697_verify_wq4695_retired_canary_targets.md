# wq#4697 verify of wq#4695 — CONFIRM

Verifier: orch-burndown-2 (orchestrator-side; fworker PERMISSION_LOOP on `bash install.sh --verify`)
Date: 2026-10-26 PDT. Target: claude-foreman PRs #2495 + #2496 (main 57c0159a), bundle ops/wq4695-watchdog-retired-canary-a1.

## Evidence
- `bash ops/wq4695-watchdog-retired-canary-a1/install.sh --verify` -> VERIFY OK (rc 0).
- harness/tests/test_wq4695_retired_canary_targets.py -> 10 passed (run with PYTHONPATH=harness; without it conftest fails on `import fworker`, which is how a bare `pytest` from the repo root looks like 10 errors).
- Live files byte-identical to the bundle: /home/lumen/bin/maint-ops/tests/{ollama,tailscale}_canary.sh and /home/lumen/bin/fleet-retired-nodes.json (nephthys, isis, hathor, khepri, wadjet with dates).
- ollama_canary.sh targets anubis (NAME=anubis LAN=192.168.8.232), refuses (exit 9) when the canary is in the retired list or unreachable.
- Ticket liveness grep (no isis/hathor/khepri/wadjet DEVICES entries in agent/lumen_watchdog.py) -> 0 matches. The lumen_watchdog half of the original tests went with wq#4701, which retired lumen-watchdog (service disabled, no timer); that is consistent, not a regression.

## Verdict
CONFIRM. Note the previous orchestrator already hand-deployed this bundle (lumen-bin PR #278) after finding it landed-but-not-installed; live state matches the bundle today.
