# wq#4149: OpenHands 1.8 hardening package

Follow-up to wq#4007 (deployed + smoke-passed 2026-10-16). Scope: the items
from `research/OPENHANDS_1_8_READINESS.md` §4 that a BUILD worker can prepare
but not execute — docker and the compose/config files at
`/home/lumen/containers/openhands/` are outside this lane's reach (`docker ps`
itself returns `Permission ... denied`, confirmed live this ticket, same wall
wq#4007/wq#4111/wq#4018 all hit).

## What's here

| File | Covers | Status |
|---|---|---|
| `reaper.py` + `test_reaper.py` | item 3 (reap idle/failed sandboxes) | Pure selection logic, 8/8 tests pass, no docker needed. `SANDBOX_NAME_PREFIX` is an **unconfirmed guess** — see the module docstring. A privileged lane must confirm the real sandbox container naming convention with `docker ps -a --format '{{.Names}}'` before trusting `reap_candidates()`'s output against this host |
| `harden_app.py` | item 2 (workspace ownership) + item 3 (app-container mem/cpu limits only) | Privileged-lane script, `--dry-run` tested here (see below); not run against the live container from this lane |

## What's NOT here, and why

- **`max_num_sandboxes=5`** (item 3, the other half): the ticket names this
  exact config knob, but this lane found no way to confirm it's the real
  OpenHands 1.8 config key without either (a) web/GitHub search — both
  returned `ok:false` this session, all general engines suspended/CAPTCHA'd,
  no `BRAVE_API_KEY` (wq#2595, already open) — or (b) reading the installed
  agent-server image's config schema, which needs `docker exec`. Guessing an
  env var name here would be exactly the failure mode instinct 8 warns about
  (a value Claude invented, then a later "it didn't work" reads as a fleet
  problem when it's really evidence the name was fabricated). Left for a
  privileged lane to confirm against the image before setting it.
- **Per-run wall-clock + cost caps** (item 4): the wall-clock half is already
  demonstrated in `ops/wq4111-openhands-litellm-production/configure_and_run_task.py`
  (`subprocess.run(..., timeout=TASK_TIMEOUT_S)`); the cost half is bounded at
  the LiteLLM virtual-key level (`openhands-2`, $10/30d per the ticket) and
  this lane has no way to read or tighten that budget — LiteLLM admin needs
  the master key, which is deliberately out of reach (wq#4018 rotated it out
  of any BUILD worker's path).
- **Docker-socket-proxy / auth for item 8** (sandbox reaches the
  unauthenticated app API on `172.17.0.1:3000`): recommendation, not
  fleet-verified (same search outage as above) — put an auth-terminating
  reverse proxy (or a project like `tecnativa/docker-socket-proxy` restricted
  to the specific API calls OpenHands needs) between the sandbox's default
  bridge and `172.17.0.1:3000`, or move the callback to a unix socket/named
  pipe the app and sandbox share instead of a TCP port reachable by anything
  else on the default bridge. This is general infrastructure knowledge, not
  something this session confirmed against OpenHands' own docs — a privileged
  lane should verify before implementing.
- **Non-root for the app container itself**: `harden_app.py` fixes
  already-written root-owned files (`chown -R`) but does not add a `user:`
  compose directive, because the app mounts `/var/run/docker.sock` and this
  lane cannot test whether the image's docker-cli calls still work under a
  non-root UID without docker access to try it.
- **T1–T8 re-benchmark** (item 6): needs `/opt/benchmark` and docker, both
  outside every `--add-dir` this lane was granted. Queued as a privileged-lane
  task, not attempted here.

## Verification run (this lane, no docker)

```
$ python3 -m unittest test_reaper.py -v
... 8 tests, OK

$ python3 -m py_compile harden_app.py reaper.py test_reaper.py
(clean)

$ python3 harden_app.py --dry-run
--dry-run: would write .../docker-compose.override.yml: [mem_limit: 2g, cpus: "2.0"]
--dry-run: would run: docker compose ... up -d --force-recreate openhands
--dry-run: would run: chown -R lumen:lumen /home/lumen/.openhands
--dry-run: no changes made
```

## What the privileged lane runs next

```
ssh lumen@<lumen ip from lumen_state.py current lumen> \
  'python3 /home/lumen/foreman/ops/wq4149-openhands-1-8-hardening/harden_app.py --dry-run'
ssh lumen@<...> \
  'python3 /home/lumen/foreman/ops/wq4149-openhands-1-8-hardening/harden_app.py'
```

Then, with docker access: confirm `SANDBOX_NAME_PREFIX` in `reaper.py` against
`docker ps -a --format '{{.Names}}'`, wire `reap_candidates()` to a live
`docker inspect` call + `docker rm -f` on each returned name, and re-run
`python3 probe.py` (in `ops/wq4111-openhands-litellm-production/`) to confirm
the app survived the recreate.
