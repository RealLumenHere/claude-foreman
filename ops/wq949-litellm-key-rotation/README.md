# wq#949 — rotating the 14 burned LiteLLM service keys

wq#948 stopped `litellm-budget-alert` putting raw keys in `GET /key/info?key=...` URLs. It
could not un-log the sweeps that already ran. Under the printed-is-burned rule from
wq#914/#920, every key in the provisioning tool's `SERVICES` is burned and needs replacing,
and the log holding the cleartext needs to go.

**Status: the rotation has not been performed.** It needs root, and the worker that opened
this has neither root nor a sanctioned path to it. The scripts here are the parts that do
not need root: the acceptance check, the per-alias smoke check, and the consumer map that
tells you what to restart between rotations. See [Blocked on](#blocked-on).

## What leaked

```
$ python3 audit_key_leak.py --json
{"ok": false, "container": "litellm", "occurrences": 30, "distinct_keys": 15,
 "clusters": ["66", "537-550", "1594", "1989-2002"]}
```

Two clusters of 14 are the 2026-08-23T18:15Z and 2026-08-24T00:15Z budget sweeps, each
printing every alias in `SERVICES`. The two singletons are individual lookups. The 15th
distinct key is one gateway alias more than `SERVICES` holds, because `ops-emergency` was
itself rotated to `ops-emergency-2` on 2026-08-23T22:32Z by wq#920 and both generations
appear in the window.

Exposure is bounded: the gateway binds `127.0.0.1:4000` and is not reachable off-host. It
is not zero — every account in the `docker` group on this host can read those keys out of
`docker logs` and spend all fourteen budgets, and that includes every fleet worker session.

## Scripts

| | |
|---|---|
| `audit_key_leak.py` | Acceptance check. Exit 1 while any `?key=sk-` survives in the container log. Reports counts and line clusters, never a key. |
| `smoke_alias.py` | Per-alias check that the key on file is one the gateway accepts, and which gateway alias it resolved to. Run before and after each rotation: the alias must increment. |
| `consumer_map.py` | Per-alias consumers, split by how each picks up a new key. Derived from the live host, so it cannot drift from this file. |

`smoke_alias.py` authenticates in the `Authorization` header and calls `/key/info` with no
query argument. Do not reintroduce the `?key=` form — that is the wq#948 leak.

## Consumers

`python3 consumer_map.py` prints the live version. As of 2026-08-24:

- **`lumen-agent`** — `lumen-planner`, `lumen-evaluator`, `lumen-generator`,
  `lumen-fleet-cerebras`, `lumen-fleet-mistral`, `lumen-fleet-openrouter`. All active, all
  hold the key in their process env via `EnvironmentFile=`. **Restart required.**
- **`lumen-messenger`** — `lumen-messenger`. Active. **Restart required.**
- **`brain-healthcheck`, `lumen-reflector`, `minerva-pipeline`** — `lumen-brain-health`,
  `lumen-reflector`, `minerva-classifier`, `minerva-eval`, `minerva-factory`,
  `minerva-proposal`. All inactive and timer-driven; systemd re-reads `EnvironmentFile=` on
  the next start. Nothing to do.
- **`lpp`, `duet`, `langfuse-evaluator`, `lumen-research`, `minerva-bench`, `sentinel`** —
  scripts that open the key file per invocation. Nothing to do.
- **`sia-news`** — a container on host `sia`, reading `/home/sia/sia-news/.env`. A local
  rotation does not reach it. Push the new key and recreate that container in the same step,
  or sia-news 401s until someone does.
- **`ops-emergency`, `lumen-dispatch`** — no consumers. `ops-emergency` is manual ops;
  `lumen-dispatch` is disarmed pending Frank's Tier-3 gate. Rotate freely.

## Runbook

`--rotate` deletes the old gateway key the moment it writes the replacement, so an active
consumer starts getting 401s the instant its alias rotates. Go one alias at a time and finish
each alias's consumers before starting the next. Do not bulk-rotate.

Before anything, confirm the alerting path is still clean, or the next sweep re-leaks the new
keys within hours:

```
python3 ../wq948-budget-alert-key-leak/ensure_no_key_in_url.py   # must exit 0
python3 smoke_alias.py                                           # baseline: 14 OK
```

`lumen-budget-alert.timer` fires every six hours at 00:15/06:15/12:15/18:15 UTC. Leave
yourself room.

Then per alias, cheapest blast radius first — `ops-emergency`, `lumen-dispatch`, the six
runtime-reader aliases, the three timer-driven aliases, `sia-news`, `lumen-messenger`,
`lumen-agent`:

```
python3 consumer_map.py "$ALIAS"                 # what this one costs
/home/lumen/bin/litellm-provision-keys --rotate "$ALIAS"
python3 smoke_alias.py "$ALIAS"                  # gateway alias must have incremented
sudo systemctl restart <each unit consumer_map.py listed under `restart`>
systemctl is-active <same units>                 # must all be active
```

For `sia-news`, replace the restart with pushing the new key into
`sia:/home/sia/sia-news/.env` and recreating the container there.

Once all 14 are rotated, drop the log. The cleartext lives in the container's json log, which
survives `docker restart`:

```
sudo truncate -s 0 "$(docker inspect --format '{{.LogPath}}' litellm)"
python3 audit_key_leak.py                        # must exit 0
python3 ../wq948-budget-alert-key-leak/ensure_no_key_in_url.py
```

Truncating in place keeps the container running. `docker rm` would also clear the log, but it
takes the gateway down and everything on this host talks to it.

## Blocked on

Root. Concretely, on this host as `lumen` under `harness/guard.sh`:

- `sudo` is denied outright (`guard.sh:84`), and `systemctl restart` on a system unit does
  not work without it: `systemctl start lumen-planner` returns *"Access denied as the
  requested operation requires interactive authentication."* Seven active units across
  `lumen-agent` and `lumen-messenger` cannot be restarted, so rotating those two aliases
  would 401 them until someone with root intervened.
- `/var/lib/docker` is `drwx--x--- root root`, so the json log cannot be truncated, and
  `guard.sh:117` denies every `docker` verb but `ps` and `logs`, so the container cannot be
  recreated either. The log purge has no unprivileged path at all.

Rotating only the twelve aliases that need no restart was rejected: it leaves the two
highest-value keys burned and live, leaves the log unpurged either way, and puts the manifest
in a half-rotated state that the next operator has to reason about. The whole sequence wants
one pass by one operator with root.
