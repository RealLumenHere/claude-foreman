# wq#2642 verify of wq#2605 (PR #1003): CONTRADICTED, bundle was never applied

Verifier: final-push-ad230d, 2026-09-29. Read-only on the live box. Judgment trigger per the ticket text.

wq#2605 closed as done with "shipped as tested-by-inspection proposal; live numbers still owed". Live state:
- `/etc/systemd/system/desktop-commander-mcp.service.d/10-control-plane-priority.conf`: absent
- `/etc/systemd/system/cloudflared.service.d/10-control-plane-priority.conf`: absent
- `/etc/systemd/system/lumen-workers.slice`: absent
- CPUWeight=1000 / IOWeight=1000 on both units come from `/run/systemd/system.control/*` (runtime only,
  lost on reboot). Nice=0, OOMScoreAdjust=0, MemorySwapMax=infinity (bundle wants -5 / -900 / 1G on DC).
- `bin/lumen-run` does pass `--slice=lumen-workers.slice` (PR #141 is live; a running job shows that slice), but with
  no slice unit the slice gets default weights, so workers are NOT deprioritised. The isolation half is a no-op.

This PR renames `apply.sh` to `install.sh` (wq#2637 precedent for wq2606) and adds `--verify`;
on Lumen it prints 3 MISS lines, exit 1. Expect ops-deploy-drift to open an `ops-drift:wq2605-control-plane-isolation`
aq row after 24h. That is the correct signal.

Not applied here: install.sh restarts desktop-commander-mcp and cloudflared, i.e. the control plane
(control-plane rule 3, Frank drives, quiet window, detached). Stress numbers (`stress_test.sh`) are still owed.
Note for the apply: lumen-workers.slice CPUWeight=50 / MemoryHigh=40G will start throttling every detached job.
