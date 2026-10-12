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
python3 /home/lumen/foreman/research/bridge.py --dry-run \
  /home/lumen/foreman/research/fixtures/sandbox/fail_smoke.json   # verdict refused, exit 1
```

Repeat with `pass_smoke.json` (expect `would_submit`, exit 0) and `egress_smoke.json`
(expect `smoke_exit` != 0: the smoke phase runs with `--network none`). The arm64 path
needs the qemu-aarch64 binfmt entry, which is registered on Lumen.

Rollback: `sudo systemctl disable --now lumen-research-sandbox.path`.
