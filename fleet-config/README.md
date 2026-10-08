# fleet-config — IaC baseline for the Lumen fleet (wq#3656)

Everything needed to provision a new fleet node, rebuild a dead one, or
recover core services **without reading old chat transcripts**.

```
fleet-config/
├── ansible/        inventory + playbooks + roles (the provisioning path)
├── docker/         compose files + .env.example per containerised service
├── systemd/        wq backup units + map of where all other fleet units live
├── scripts/        bootstrap / backup / restore / inventory regeneration
└── docs/           RECOVERY.md, ARCHITECTURE.md, RUNBOOK.md
```

## Quickstart: provision a new node

```bash
# all commands run ON lumen (the control node) — SSH in first
cd /home/lumen/foreman/fleet-config
./scripts/gen-inventory.sh            # refresh IPs from lumen_state.py
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml \
  --limit <new-node> -K
```

Full walkthrough: [docs/RUNBOOK.md](docs/RUNBOOK.md).
Disaster recovery (wq, LiteLLM, monitoring, credentials): [docs/RECOVERY.md](docs/RECOVERY.md).
Node/service map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Authoritative sources — what this repo does and does not own

This repo is the *rebuild path*. Some artifacts have a live authoritative
copy elsewhere; the table says which wins.

| artifact | authoritative copy | fleet-config holds |
|---|---|---|
| foreman dispatcher units | `foreman/dispatcher/systemd/` (this repo, one level up) | install procedure only (`systemd/README.md`) |
| LiteLLM config | `/home/lumen/agent/litellm_config.yaml` on lumen (credential-bearing, never committed) | regeneration procedure + skeleton (`docs/RECOVERY.md` §4) |
| wq database | `/home/lumen/agents/work-queue/queue.db` (sqlite, live) | backup/restore scripts + units |
| gitea | native install on anubis, `/etc/gitea/app.ini` | compose rebuild path (`docker/gitea/`) |
| node IPs | `lumen_state.py current <node>` (tailnet IPs rotate) | generated inventory; never hand-edit IPs |

## Rules that bind every change here

- **Thor is hands-off** (`100.112.151.100`): no restart/reboot/provisioning
  verbs, ever. It is in the inventory only as `readonly`.
- **No credentials in this repo.** `.env.example` files carry variable names
  and empty values only. Real values live in 0600 files on the target node
  (see `docs/RECOVERY.md` §6 for how to re-mint each one).
- **Lumen is headless**: every command in these docs runs on a fleet node via
  SSH, never on a console.
