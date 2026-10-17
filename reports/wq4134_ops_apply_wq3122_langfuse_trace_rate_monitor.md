# wq#4134 — ops-root-apply wq#3122 langfuse-trace-rate-monitor install

wq#3130 closed with `install.sh --verify` FAILING (timer not enabled/active) because
install requires root and BUILD workers can't `sudo`. This ticket runs the bundle's
privileged install via the approved root path
(`/home/lumen/foreman/ops/wq3122-litellm-langfuse-trace-drop/install.sh:14-17`, which
on `--verify` checks `venabled`/`vactive` on `langfuse-trace-rate-monitor.timer`), then
re-runs `--verify` to confirm.

## What I did
- `/home/lumen/bin/lumen-run start --root --name wq4134-install -- bash
  /home/lumen/foreman/ops/wq3122-litellm-langfuse-trace-drop/install.sh` — installs
  the two systemd unit files
  (`ops/wq3122-litellm-langfuse-trace-drop/install.sh:27-30`) and runs
  `systemctl enable --now langfuse-trace-rate-monitor.timer`
  (`ops/wq3122-litellm-langfuse-trace-drop/install.sh:33`). Unit exited `DONE:0`;
  journal showed the timer symlinked into `timers.target.wants` and the first
  `list-timers` pass already scheduled.
- Re-ran `bash .../install.sh --verify` (via `lumen-run`, non-root) per the ticket's
  confirmation instruction. Output: `VERIFY OK`, exit 0 — `venabled`/`vactive` on
  `langfuse-trace-rate-monitor.timer` now both pass
  (`ops/wq3122-litellm-langfuse-trace-drop/install.sh:19-23`).

## Notes
- No code change needed: wq#3130's bundle was already correct, it just required a
  privileged apply step this bundle's own header already documents
  (`ops/wq3122-litellm-langfuse-trace-drop/install.sh:2-3`: "Run as root via
  `lumen-run --root`"). `sudo` itself is denied by the guard hook
  (`BLOCKED — denied verb (sudo/...)`); `lumen-run --root` is the sanctioned
  equivalent for exactly this case and is allowlisted for BUILD workers.
