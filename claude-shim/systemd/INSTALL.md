# Staged install — claude-shim (wq#726)

The build ships this unit **repo-only** (writing /etc is outside worker scope — the
guard denies `sudo` outright, and the fleet's own precedent, `dispatcher/systemd/INSTALL.md`
for AUTO-3, follows the same rule). Nothing below is run by the fleet automatically;
`research/CLAUDE_SHIM_DESIGN.md` is the design contract.

## 1. Install, WITHOUT enabling (staged)

```bash
sudo cp /home/lumen/foreman/claude-shim/systemd/claude-shim.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl is-enabled claude-shim.service   # expect: static (no [Install] section)
```

## 2. Provision the shared secret LiteLLM will present

The shim auto-generates `claude-shim/.api_key` (0600, gitignored) on first run if
`CLAUDE_SHIM_API_KEY` isn't set in its environment. For the systemd-managed instance,
either let it self-generate and read the value back out of that file, or set it
explicitly via a drop-in:

```bash
sudo systemctl edit claude-shim.service
# [Service]
# Environment=CLAUDE_SHIM_API_KEY=<paste a generated secrets.token_urlsafe(32)>
```

Whatever value is live, add the same value to `/home/lumen/agent/.env` as
`CLAUDE_SHIM_API_KEY=...` — the litellm container's `env_file` already points at that
file, and the `claude-max-sonnet`/`claude-max-opus` litellm_config.yaml entries read
`os.environ/CLAUDE_SHIM_API_KEY`.

## 3. Start it

```bash
sudo systemctl start claude-shim.service
systemctl status claude-shim.service
curl -s http://127.0.0.1:8091/health   # expect: {"status": "ok"}
```

## 4. Pick up the LiteLLM config entries

The `litellm` container only reads `litellm_config.yaml` at startup — restarting it is
Frank's call (a shared service other live consumers depend on; the guard denies all
`docker` subcommands except `ps`/`logs`, so a worker session cannot restart it). The
build phase registered `claude-max-sonnet`/`claude-max-opus` live via the Model
Management API (`POST /model/new`) for verification without a restart; that
registration does not survive a container restart. To make it durable:

```bash
# next natural litellm restart/redeploy picks up the yaml entries already committed
```

or restart it by hand when convenient:

```bash
docker compose -f /home/lumen/docker/docker-compose.yml restart litellm
```

## Rollback

```bash
sudo systemctl stop claude-shim.service
# litellm_config.yaml entries are inert (unreachable backend) if the shim isn't running —
# no separate litellm-side rollback needed. A timestamped .bak of the pre-edit config is
# alongside the live file if a full revert of the yaml is ever wanted.
```
