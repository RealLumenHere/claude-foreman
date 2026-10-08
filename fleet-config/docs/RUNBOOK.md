# RUNBOOK — provisioning a new fleet node, end to end

Goal of this file: a new node joins the fleet with **no chat-transcript
archaeology**. Everything is either here, in RECOVERY.md, or in the
referenced authoritative files.

## 1. Prerequisites (one-time, mostly physical → Frank tasks)

- Hardware on the LAN, OS installed, user `lumen` with sudo.
- Mind the wifi bridge (FLEET_MAP.md): no tens-of-GB pulls (ollama models,
  large images) across the router-to-router bridge without checking which
  side the node is on first.
- SSH server up; lumen's pubkey in the new node's `authorized_keys`.
- M80-class nodes (Intel vPro/AMT, dual-NVMe) have node-specific physical
  steps before any of the above is reachable — MEBx/AMT config and OS
  install target the wrong disk if improvised. See
  [M80_PHYSICAL_BRINGUP.md](M80_PHYSICAL_BRINGUP.md) (wq#3703).

## 2. Tailscale join

```bash
# on the new node
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # auth via key from the admin console (Frank)
tailscale ip -4              # record; lumen_state.py picks it up
```

Confirm from lumen: `/home/lumen/bin/lumen_state.py current <node>`.

## 3. Inventory + provision

```bash
# on lumen
cd /home/lumen/foreman/fleet-config
./scripts/gen-inventory.sh                  # refresh IPs from lumen_state.py
# add the node under the right group in ansible/inventory/hosts.yml if new
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml \
    --limit <node> -K
```

Group choice decides what lands (see ARCHITECTURE.md node table). A plain
worker/copy node needs only `workers` or bare `common`. The playbook fails
closed wherever a credential-bearing file is missing — each failure message
names the RECOVERY.md section that produces the file; fix, re-run (the
playbook is idempotent).

## 4. Verify

```bash
ansible <node> -i ansible/inventory/hosts.yml -m ping
ssh lumen@<node-ip> 'git -C /home/lumen/foreman log --oneline -1'
# gateway nodes only:
curl -s http://127.0.0.1:4000/health/liveliness        # LiteLLM
curl -s http://127.0.0.1:8418/health                   # gatus
systemctl list-timers | grep -E 'lumen-foreman|wq-backup'
```

Add the node's endpoints to the gatus config (RECOVERY.md §5) so the fleet
notices next time it dies.

## 5. Day-2

- **Backups**: nightly via `wq-backup.timer` on lumen; spot-check
  `ls -1dt /home/lumen/backups/fleet-* | head -1` is < 24h old.
- **Config drift**: any change made live on a node (new unit, compose
  edit) gets mirrored into this directory or explicitly listed as
  live-only in `systemd/README.md`'s table — that table is the contract
  for what a rebuild can and cannot regenerate from git alone.
- **Standing constraints** (always, including day-2): thor untouched;
  maat never-restart; sobek's postgresql/cloudflared via detached
  systemd-run only; no credentials in this repo, ever.
