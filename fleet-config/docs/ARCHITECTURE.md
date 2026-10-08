# ARCHITECTURE — nodes and services

Sources: `foreman/research/FLEET_MAP.md` (roster),
`foreman/research/FLEET_SERVICE_ATLAS.md` (lumen service graph, wq#1627),
`foreman/harness/fleet-exec-policy.yaml` (what may act on what). IPs rotate;
authoritative lookup is `lumen_state.py current <node>`.

## Nodes

| node | role | provisioning stance |
|---|---|---|
| lumen | T5810. Control node + gateway: LiteLLM (:4000), wq, foreman dispatcher, docker (~35 containers), redis, gatus (:8418), netdata | full: common + litellm + monitoring + workers roles |
| isis | Ollama / GPU inference | common role; ollama managed natively (restart allowed per exec-policy) |
| sobek | postgresql / cloudflared | common role only — its two services change only via detached systemd-run |
| anubis | gitea (native, `/etc/gitea/app.ini`) | common + gitea role (rebuild path flag-gated) |
| maat | restart-locked ("*" never-restart) | common role; file installs only, no service restarts |
| bes | copy target (off-host backup destination) | common role |
| thor | **Frank's workstation — tier-0 hands-off** | never targeted; inventory `readonly` group is documentation only |
| ptah, sekhmet, horus, neith, khnum | read-only ssh (exec-policy) | not targeted |
| hathor | auto-sleeps by design | not targeted; offline pages are not incidents |

## Service dependency reality (lumen)

From the service atlas: systemd coupling is deliberately loose (`After=`/
`Wants=` only, almost no `Requires=`), so the real dependency graph is TCP
ports, not unit edges:

- **LiteLLM :4000** — docker container `litellm` (there is NO
  `litellm.service`; units naming it have a dangling target). Config:
  `/home/lumen/agent/litellm_config.yaml`. Every model-calling fleet
  process depends on this port.
- **wq** — no daemon at all: the `wq` CLI over sqlite
  `agents/work-queue/queue.db`. Depends on nothing but the filesystem.
- **foreman dispatcher** — `lumen-foreman-*` units
  (`foreman/dispatcher/systemd/`): dispatch, gate@, health(+window),
  janitor, mortician, selfheal, burnrate, window-gate, nightly-gate.
  Depend on wq (db) + LiteLLM (:4000) + the claude CLI.
- **monitoring** — gatus container :8418 + `gatus-ntfy-relay.service`
  :8784 + `gatus-sshauth-probe.service` + netdata + cron-deadman stamps.
- **gitea (anubis)** — serves the foreman repo
  (`http://<anubis>:3000/lumen/claude-foreman.git`); lumen holds the token
  in `~/.git-credentials` via credential helper.

## Repo layout decision

`fleet-config/` lives inside the foreman repo (this directory) rather than
as a separate gitea repo: foreman already carries the authoritative
dispatcher units and research docs this baseline references, and a single
clone must be enough to rebuild lumen. Splitting it out later is a
`git filter-repo` away and changes nothing above.
