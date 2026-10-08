# RECOVERY — fleet disaster playbook

Each section is self-contained and assumes the worst plausible state for
that subsystem. All commands run on the named node over SSH (the fleet is
headless). IPs: `/home/lumen/bin/lumen_state.py current <node>` (invoke the
shim directly — it is a shell wrapper, not a python file) — never trust a
remembered IP.

## §1 Restore from backup

Backups: `/home/lumen/backups/fleet-<timestamp>/` on lumen, written nightly
by `wq-backup.timer` → `scripts/backup.sh`. Each snapshot holds `queue.db`
(consistent sqlite copy), `work-queue-tooling.tgz`, `configs.tgz` (litellm
config, compose dirs, `/etc/systemd/system`, secrets), `crontab.txt`.

```bash
ls -1dt /home/lumen/backups/fleet-* | head -3     # pick newest sane snapshot
/home/lumen/foreman/fleet-config/scripts/restore.sh \
    /home/lumen/backups/fleet-<TS> --all
```

Verify after: `wq list` answers; `curl -s http://127.0.0.1:4000/health/liveliness`;
`systemctl list-timers | grep lumen-foreman` shows the dispatcher timers.

If lumen's disk is gone entirely, do §2 (rebuild) first on the replacement
host, then run this section — restore.sh only needs bash, sqlite3, tar.
Off-host copy of the backup dir is bes (the designated copy target); if
that sync wasn't running, the newest on-disk snapshot is all there is.

## §2 Rebuild services (dead node / fresh OS)

1. Base OS + user `lumen` + SSH + tailscale join (RUNBOOK.md §2).
2. From lumen (or the rebuilt node itself once the foreman repo is cloned):
   ```bash
   cd /home/lumen/foreman/fleet-config
   ./scripts/bootstrap.sh <node>
   ```
   This runs `site.yml --limit <node>`: packages, dirs, repo clone,
   then the node's service roles (litellm/monitoring/workers on lumen,
   gitea on anubis, nothing service-level on sobek/maat/bes yet).
3. The roles fail closed on missing credential-bearing files
   (litellm config, gatus config, .env files) — that is the signal to run
   §1 (restore) or §4/§5/§6 (regenerate) and re-run the playbook.
4. Gitea on anubis: only pass `-e gitea_rebuild=true` if the native install
   (`/etc/gitea/app.ini`) is actually gone — the flag exists so a routine
   run can never shadow the live gitea with an empty container.
5. Constraints that survive any rebuild: **thor is never touched**;
   maat is never-restart (install files, skip service starts); sobek's
   postgresql/cloudflared only change via detached systemd-run.

## §3 Recover wq

wq is one python script + one sqlite db, no git repo, no daemon:

- tool: `/home/lumen/agents/work-queue/wq` (symlinked/copied to `/home/lumen/bin/wq`)
- db: `/home/lumen/agents/work-queue/queue.db` (`DB = ...` at `wq:4`)

Recovery order:
1. `restore.sh <snap> --wq` — restores db (with pre-restore copy of any
   live db) + tooling tar, runs `PRAGMA integrity_check`.
2. If the db is corrupt in every snapshot: `sqlite3 queue.db ".recover" |
   sqlite3 queue.recovered.db`, inspect, then swap in.
3. If there is no backup at all, the schema can be rebuilt from the `wq`
   script itself (it creates/queries all columns it needs) — tickets are
   lost, structure is not. Re-file open work from the dispatcher logs
   (`foreman/dispatcher/` journal: `journalctl -u lumen-foreman-dispatch`).
4. Verify: `/home/lumen/bin/wq list` and a round-trip
   `wq add test --notes smoke` → `wq done <id>`.

## §4 Regenerate LiteLLM config

Live config: `/home/lumen/agent/litellm_config.yaml` (0600, on lumen).
Never committed; the container mounts it read-only
(`docker/litellm/docker-compose.yml`).

1. Prefer restore: it is inside `configs.tgz` of every snapshot (§1).
2. From scratch:
   - Skeleton: `model_list` entries (one per routed model: anthropic
     claude-* via ANTHROPIC_API_KEY, openrouter + cerebras + mistral
     entries, ollama models pointing at isis), `general_settings:
     master_key: os.environ/LITELLM_MASTER_KEY`.
   - Fill provider keys per §6 into `docker/litellm/.env` (0600).
   - The model roster the fleet expects = whatever
     `foreman/harness/` model pools reference; grep the foreman repo for
     `model:` values (e.g. `claude-fable-5`) and make each resolvable.
3. Apply: `cd /home/lumen/fleet-compose/litellm && docker compose up -d`
   (or the litellm playbook). Verify:
   `curl -s http://127.0.0.1:4000/health/liveliness` → 200, then one real
   completion through a sentinel key.
4. Any edit to the live config: back up first
   (`cp litellm_config.yaml litellm_config.yaml.bak.$(date -u +%Y%m%dT%H%M%SZ)`
   — the existing `.bak.*` convention).

## §5 Re-establish monitoring

Stack on lumen: gatus container (:8418, endpoint config in
`fleet-compose/monitoring/gatus/config.yaml`), `gatus-ntfy-relay.service`
(:8784, relays gatus alerts to ntfy), `gatus-sshauth-probe.service`,
netdata (distro), plus the cron-deadman stamps
(`gatus-health-checks/cron_deadman.py`).

1. `ansible-playbook ... playbooks/monitoring.yml` — installs netdata,
   deploys compose, starts gatus; fails closed if the gatus config is
   missing → restore it from `configs.tgz` (§1).
2. Relay units arrive via the `/etc/systemd/system` part of §1's restore:
   `sudo systemctl enable --now gatus-ntfy-relay gatus-sshauth-probe`.
3. Verify end-to-end, not just green pages: temporarily point one gatus
   endpoint at a dead port, confirm the ntfy notification arrives, revert.
4. Known non-incident: hathor pages "offline" when it auto-sleeps — that
   endpoint is expected to flap (selfheal ignores it by design).

## §6 Credential recovery

Rule: a credential printed is a credential burned — rotate same day. All
re-mints below land in 0600 files, never in URLs or this repo.

| credential | where it lives | how to re-mint |
|---|---|---|
| gitea token | `~/.secrets/gitea-lumen.token` + `~/.git-credentials` | **No password needed.** Shell onto anubis and use the host CLI: `gitea --config /etc/gitea/app.ini admin user generate-access-token` with the scopes you need (write:repository,write:user,write:organization). Standing fleet-autonomous procedure (ISSUE-LOG #144). |
| LiteLLM master key | `docker/litellm/.env` → `LITELLM_MASTER_KEY` | Generate fresh (`openssl rand -hex 32`), update .env, restart container, update every fleet client that holds a derived/virtual key. |
| LiteLLM virtual/sentinel keys | issued by LiteLLM | re-issue via `/key/generate` against the new master key. |
| provider keys (Anthropic, OpenRouter, Cerebras, Mistral) | `docker/litellm/.env` | re-mint in each provider console — this is a Frank physical task (personal accounts); queue it as such, work everything else meanwhile. |
| ntfy token/topic | `docker/monitoring/.env` | re-create topic/token on the ntfy host or service. |
| tailscale node key | tailscaled state | `tailscale up` re-auth with a fresh auth key from the admin console (Frank physical task). |
| SSH keys | `~/.ssh` | regenerate per node, redistribute pubkeys to `authorized_keys` on peers; update any `fleet-exec-policy.yaml`-governed paths. |

After any re-mint: run the masking sweep before pasting anything into a
terminal, and append the rotation to kb/ISSUE-LOG.md.
