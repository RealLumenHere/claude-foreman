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
| `reaper.py` + `test_reaper.py` | item 3 (reap idle/failed sandboxes) | Pure selection logic, 15/15 tests pass, no docker needed. `SANDBOX_NAME_PREFIX` **confirmed live 2026-10-18** (wq#4199): real sandbox container names are `oh-agent-server-<id>`, not the `openhands-runtime-` guess this shipped with (which matched nothing). Kill step now targets the supported API path — `reap_request()` builds `DELETE /api/v1/sandboxes?sandbox_id=<id>` (confirmed live: 200, removed in <4s; the bare path form returns 422) — with `docker rm -f <name>` left as the privileged lane's fallback only |
| `harden_app.py` | item 2 (workspace ownership) + item 3 (app-container mem/cpu limits only) | Privileged-lane script, `--dry-run` tested here (see below); not run against the live container from this lane |

## What's NOT here, and why

- **`max_num_sandboxes=5`** (item 3, the other half): **still unconfirmed as
  of wq#4199** (re-checked 2026-10-18) — `fleet-search web`/`github` both
  still return `ok:false` this session (general engines suspended/CAPTCHA'd,
  no `BRAVE_API_KEY` per wq#2595; github 401, no usable token), and reading
  the installed agent-server image's config schema needs `docker exec`,
  which this lane doesn't have (`docker ps` itself denied — see wq#4152,
  blocked on the same wall, ISSUE-LOG row 1399). Guessing an env var name
  here would be exactly the failure mode instinct 8 warns about (a value
  Claude invented, then a later "it didn't work" reads as a fleet problem
  when it's really evidence the name was fabricated). Left for a privileged
  lane to confirm against the image before setting it.
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
... 15 tests, OK

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

Then, with docker access: wire `reap_candidates()` to a live `docker inspect`
call, and for each name it returns call `reaper.reap_request(name)` — builds
`DELETE /api/v1/sandboxes?sandbox_id=<id>`, confirmed live wq#4199 — falling
back to `docker rm -f <name>` only if that request errors. Scheduling
(detached `systemd-run`, per instinct 5) stays in wq#4152's scope, not this
package. Re-run `python3 probe.py` (in
`ops/wq4111-openhands-litellm-production/`) to confirm the app survived.

## Privileged-lane follow-up

Everything in "What's NOT here, and why" above needs docker/host access this
BUILD lane doesn't have. Filed as wq#4152 (lane `fleet`, deps on this ticket)
rather than left as an unresolved gate — see that ticket for the full list
(items 2/3/4/6/8/9).
