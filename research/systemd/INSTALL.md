# Staged install — lumen-research-sandbox (wq#3759)

Shipped repo-only: writing /etc is outside worker scope, and workers may not invoke the
container runtime (ISSUE-LOG 1262). `ops/systemd_desired_state.py staged` lists these
units until they are live.

```bash
mkdir -p /home/lumen/foreman/research/proposals/pending   # as lumen, before the .path unit
sudo cp /home/lumen/foreman/research/systemd/lumen-research-sandbox.{service,path} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-research-sandbox.path
```

## Live acceptance (container backend)

```bash
cp /home/lumen/foreman/research/fixtures/sandbox/fail_smoke.json /home/lumen/foreman/research/proposals/pending/
journalctl -u lumen-research-sandbox.service -n 5           # {"proposal": ..., "smoke_exit": 1}
python3 /home/lumen/foreman/research/bridge.py submit --dry-run \
  /home/lumen/foreman/research/fixtures/sandbox/fail_smoke.json   # verdict refused, exit 1
```

Repeat with `pass_smoke.json` (expect `would_submit`, exit 0) and `egress_smoke.json`
(expect `install_exit` 0 and `smoke_exit` 1: install runs the same connect with network as
its control, the smoke phase runs with `--network none`; 127 means the probe binary is
missing, not that egress was cut). `pass_smoke_arm64.json` covers the arm64 path (expect
`arch` arm64, `smoke_exit` 0; the smoke also checks `uname -m` is aarch64), which needs the
qemu-aarch64 binfmt entry registered on Lumen.

Installed and enabled on Lumen; all four fixtures passed through the container backend on
2026-10-17 (wq#3767).

Rollback: `sudo systemctl disable --now lumen-research-sandbox.path`.

# Staged install — lumen-research-trigger (wq#3761)

Shipped repo-only, same posture as the gate above: ships disabled, Frank enables.

```bash
sudo cp /home/lumen/foreman/research/systemd/lumen-research-trigger.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-research-trigger.timer
```

## Live acceptance

```bash
python3 /home/lumen/foreman/research/trigger.py ask "does qemu-user cover gfx1013?" --dry-run
# -> {"brief_id": "ask-...", "brief_path": ".../briefs/ask-....md", ...,
#     "verdict": "would_dispatch"}; the brief has the live rejection ledger's
#     constraint block (if any) prepended ahead of "Question:".
journalctl -u lumen-research-trigger.service -n 5
```

Rollback: `sudo systemctl disable --now lumen-research-trigger.timer`.
