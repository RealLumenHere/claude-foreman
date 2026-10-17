# wq#4111: OpenHands production deployment — LiteLLM connection + test task

## State at dispatch

OpenHands container running, never completed a task (openhands.db: 0 conversations,
0 sandboxes — wq#4007). wq#4007's diagnosis (research/openhands-deployment.md, not in
a BUILD worker's allowed dirs, recapped here from the ticket body):

- The container sits on compose network `openhands_default` and points at
  `host.docker.internal:4000`, but LiteLLM is published only on `127.0.0.1` + the
  tailnet IP, so that name never resolves to something reachable from inside
  `openhands_default`.
- A throwaway container on `docker_default` reached LiteLLM fine over the
  container-DNS name `litellm` — 38 models visible, `deepseek-v4-pro` completion OK.
- wq#4018 (landed, commit `4c53308b`) rotated the burned LiteLLM key and moved it out
  of the compose into a 0600 `.env`, and closed the Read/Grep leak path on the compose
  file. The credential side is handled; the network-attach bug it never touched is
  still open.
- Port 3000 published on `0.0.0.0`, container runs as root with `docker.sock`
  mounted, no host firewall, UI auth unverified.

## What a BUILD worker can and can't do here

Every privileged step (docker, the compose file, the LiteLLM master key) is denied to
this lane — confirmed live, not assumed: `docker ps` on this ticket returned
`Permission to use Bash with command docker ps has been denied`, the same signature
`/home/lumen/foreman/_runs/wq4007/failure.json` recorded for the previous attempt.
`/home/lumen/containers/openhands/` is also outside every `--add-dir` this worker was
granted. This mirrors wq#4018's rotation, which hit the identical wall and landed a
script + README for a privileged lane to run rather than guessing past the sandbox.

What a BUILD worker CAN do: talk to whatever is already published on the host over
plain HTTP. `probe.py` does exactly that — no docker, no secrets, no compose read —
and is real, run, evidence (see below), not a guess:

```
$ python3 probe.py
litellm /health/readiness: OK
openhands UI root: OK
openhands API routing (GET /api/conversations): spa_fallback
{"litellm_ready": true, "openhands_up": true, "api_routing": "spa_fallback"}
```

New lead beyond wq#4007: `GET /api/conversations` returns the SPA's static
`index.html` (200, `text/html`, same content-length as `/`) while `POST` to the exact
same path returns a real FastAPI `405 Method Not Allowed` (`application/json`). The
405 proves the API router has a route registered at that path; the GET behavior means
something ahead of it in the ASGI app (most likely a `StaticFiles(html=True)`
catch-all mounted before the API router) swallows GET before the router sees it. That
is a concrete, reproducible candidate explanation for "never completed a task" that
host-side probing alone can confirm or rule out — a lane with image/container access
needs to read the ASGI app's route registration order to settle it; not guessed here.

This also closes wq#4007 TODO item 9 (add a real `/alive` probe to lumen_state, not
`/api/health`, which is SPA HTML): `probe.py` is that probe. `test_probe.py` covers the
response-classification logic with mocked HTTP (4/4 pass, no live services needed).

## What the privileged lane runs next

`configure_and_run_task.py` (same posture as `ops/wq4018-openhands-key-rotation/rotate.py`:
written here, run from a container-capable operator lane):

1. Inspect the live container's networks + env (idempotent: skips straight to
   verification if already wired).
2. `docker network connect docker_default openhands` — does NOT touch LiteLLM's own
   publish bindings (wq#4007 was explicit: do not widen those).
3. Write `LLM_BASE_URL=http://litellm:4000/v1` and `LLM_MODEL=deepseek-v4-pro` (the
   model wq#4007's throwaway-container test verified, not a fresh guess) into the
   `.env` beside the compose, then `docker compose up -d --force-recreate`.
4. Re-run `probe.py`'s checks until both services are back up.
5. Verify the container's OWN network namespace can reach LiteLLM
   (`docker exec openhands curl .../litellm:4000/health/readiness`) — the check
   wq#4007 never reached; host-side reachability proves nothing about the container's
   namespace.
6. Verify the installed headless CLI's invocation with `--help` before using it
   (doctrine: never guess an unpinned image version's flags), then run ONE bounded
   test task: capped `--max-iterations`, `--no-auto-continue` (the overnight-autonomous
   posture wq#4111 asks to verify), a wall-clock timeout.
7. Confirm the task is recorded: the conversation count in `openhands.db` goes from
   N to N+1. Exits non-zero — "not production ready" — if it doesn't.

Still open after this script, left for the privileged lane's judgment (out of scope
for a config/test-task change, each is a separate hardening item from wq#4007's list):
bind the UI port to `127.0.0.1` instead of `0.0.0.0`, drop the `docker.sock` mount,
run the container non-root, pin the agent-server image version against the installed
SDK, re-run the full T1-T8 suite (T7/T8 x3) against this stack.

```
ssh lumen@<lumen ip from lumen_state.py current lumen> \
  'python3 /home/lumen/foreman/ops/wq4111-openhands-litellm-production/configure_and_run_task.py --dry-run'
ssh lumen@<...> 'python3 /home/lumen/foreman/ops/wq4111-openhands-litellm-production/configure_and_run_task.py'
```
