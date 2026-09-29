# wq#2605 — control-plane CPU/IO isolation

Implements `/home/lumen/instructions/drafts/MCP_CONTROL_PLANE_HA_DESIGN.md` §3.3's
"Worker-load isolation on Lumen" item: worker storms starved the control plane
(2026-09-25 04:28Z, load 104/44 cores, every `:3100` request "context canceled",
`desktop-commander-mcp` 16G swap peak).

## What's in this directory

- `desktop-commander-mcp.conf` / `cloudflared.conf` — persistent drop-ins:
  `CPUWeight=1000 IOWeight=1000 Nice=-5 OOMScoreAdjust=-900` on both units,
  `MemorySwapMax=1G` on `desktop-commander-mcp` only (per the ticket — cloudflared
  never showed the swap symptom).
- `lumen-workers.slice` — new slice: `CPUWeight=50 IOWeight=50 MemoryHigh=40G`.
  Job/worker processes live here so they compete for CPU/IO at 50:1000 against
  the control plane instead of 1:1 on the default `system.slice`.
- `install.sh` (was apply.sh; answers `--verify`, wq#2642) / `rollback.sh` — root-run installer/uninstaller, detached
  `systemd-run` restarts, `.pre-ha.bak` backups, health-check gate.
- `stress_test.sh` — the ticket's test-to-failure: `stress-ng --cpu 44 --io 8`
  for 300s inside `lumen-workers.slice`, concurrent 1s curl loop against
  `desktop-commander-mcp`'s `tools/list`, reports p95 (target < 5s).

## Verified live state (systemctl cat, read-only, 2026-09-25)

Two things the ticket's own text got wrong, corrected here rather than guessed:

1. **`CPUWeight=1000`/`IOWeight=1000` are already live** on both units, via
   `systemctl set-property --runtime` (shows up as `/run/systemd/system.control/
   50-CPUWeight.conf` / `50-IOWeight.conf` in `systemctl cat`) — an ad-hoc
   response to the 04:28Z incident that does **not** survive a reboot or a
   `daemon-reload` that clears `/run`. This bundle's job is to make that
   persistent (`/etc/systemd/system/*.d/`) and add the three properties that
   were never applied at all: `Nice`, `OOMScoreAdjust`, `MemorySwapMax`.
2. **`desktop-commander-mcp.service` already carries `MemoryHigh=20G` /
   `MemoryMax=26G`** in its base unit (`systemctl cat desktop-commander-mcp`),
   not the `MemoryHigh=8G` the ticket text assumed. The ticket's own instruction
   was "keep MemoryHigh=8G" (i.e. don't touch it) — this bundle takes that
   literally and does not redeclare `MemoryHigh` at all, so the live 20G/26G
   ceiling is untouched either way.

`lumen-workers.slice` does not exist yet (`systemctl cat lumen-workers.slice` →
"No files found").

## lumen-run wiring

`bin/lumen-run:83` (separate repo, `/home/lumen/bin`, not part of this worktree)
now passes `--slice=lumen-workers.slice` in its `systemd-run` `start` path
unconditionally — every `lumen-run start` job (long jobs run over Commander) is
a worker/job by definition, never a control-plane process (the control plane
runs as its own persistent `desktop-commander-mcp.service`/`cloudflared.service`,
never through `lumen-run`).

The ticket separately asks for "same for foreman fworker spawn path (grep
systemd-run in foreman/)". Verified by reading the actual spawn code
(`orchestrator/router.py:1198`): foreman's fworker BUILD/verify sessions are
spawned via `["lumen-run", "start", "--name", f"fworker-wq{ticket_id}", ...]` —
i.e. they already route through `lumen-run`, not a separate direct
`systemd-run` call. The other `systemd-run` call sites under `foreman/`
(`harness/fworker.py:4533` dispatch-refill kick, `harness/node_ports.py:120`
remote port actions, `harness/node_render_gateway.py:262` container restart,
`dispatcher/dispatch_recover.py:842` a `claude auth status` probe) are not
job/worker spawns — they're short control operations, not the CPU/IO-heavy
class this ticket targets — so they're left on the default slice. One code
change (`bin/lumen-run`) covers both halves of the ticket's ask.

## Why this ships as a proposal, not a live install

This BUILD worker's sandbox denylists the privilege-escalation verb outright
(ISSUE-LOG #68, reconfirmed by wq#2606/ISSUE-LOG #731 on the sibling
mcp-gateway-watchdog ticket from the same design doc): `install -D` to
`/etc/systemd/system/*`, `systemctl daemon-reload`, and `systemctl restart
<real-unit>` are all root-only, and the worker Bash allowlist only grants
read-only `systemctl` verbs (`status`/`is-active`/`is-enabled`/`show`/`list-`/
`cat`) plus one unrelated exception (`restart lumen-messenger`). This is
structural, not a missing grant on this ticket specifically — no worktree or
`--add-dir` fix changes it.

Following the same pattern as `ops/wq1698-supergateway-log-redaction/` and
`ops/wq2606-mcp-gateway-watchdog-a1/`: everything that needs root is shipped
here as a tested-by-inspection proposal; `bin/lumen-run` (owned by `lumen`,
no root needed) is edited directly and lands as its own PR.

## To actually apply and test (needs a root-capable session on Lumen)

```
sudo ops/wq2605-control-plane-isolation/install.sh
sudo ops/wq2605-control-plane-isolation/stress_test.sh 300
```

Record the printed p95/max/non-200 numbers on wq#2605. Rollback:
`sudo ops/wq2605-control-plane-isolation/rollback.sh`.
