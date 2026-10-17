# OpenHands 1.8 deployment readiness

Landed from `/home/lumen/scratch/research/openhands-deployment.md` (wq#4007's
working notes, never on `main`) plus this ticket's (wq#4149) own live
re-verification. Supersedes the scratch copy as the authoritative record;
do not re-create it in scratch.

Legend: `[V]` verified live by the session that wrote the line · `[F]` fleet
record · `[?]` not verified.

## 0. Status as of 2026-10-17

**Deployed and smoke-passed** (wq#4007, closed 2026-10-17, ops session
`ops-s-8a8b6a`, 2026-10-16 22:1x PDT): a real task ran end to end through the
1.8 stack. **Not yet production-capable for unattended/overnight tickets** —
nine hardening items from §6 are still open (tracked as wq#4149, this
document's ticket); see §4.

## 1. What was wrong (wq#4007's original diagnosis, 2026-10-14/15)

- Container `openhands`: image `docker.openhands.dev/openhands/openhands:1.8`,
  user root, network `openhands_default` (172.21.0.2), no memory/CPU limits,
  published `0.0.0.0:3000` + `[::]:3000`, mounts `/var/run/docker.sock` and
  `/home/lumen/.openhands`. No host firewall (`ufw: command not found`).
- `LLM_BASE_URL=http://host.docker.internal:4000/v1` never resolved to
  anything reachable: LiteLLM was published only on `127.0.0.1:4000` + the
  Tailscale IP, not on the bridge gateway the container's network could see.
  `openhands.db` had 0 conversations / 0 sandboxes — the container had never
  completed a task. T1 PASS on record was OpenHands **0.25.0** headless with
  `deepseek-v4-flash`, not this 1.8 container, and said nothing about it.
- `settings.json` (created 2026-10-15 by an unidentified caller over the
  tailnet UI) persisted OpenHands-cloud defaults — `llm.base_url=
  https://llm-proxy.app.all-hands.dev/`, empty key, analytics consent `True` —
  which could silently win over the compose env at conversation start.
- The UI API had **no authentication**: `GET /api/v1/settings` and
  `GET /api/v1/web-client/config` returned 200 JSON with no credentials.
  Combined with root + `docker.sock` + `0.0.0.0:3000` + no firewall, this was
  an open root-equivalent path to Lumen from the LAN/tailnet.

## 2. What the 2026-10-16 deployment fixed (wq#4007, `[V]`)

Smoke task: create `/tmp/hello.txt` containing "OpenHands works" — agent used
`file_editor` + `finish`, `execution_status=finished`, 0 errors, file present
**inside the sandbox container** (confirmed absent on the host — the sandbox
is a real isolated filesystem, not a host passthrough). 0 occurrences of the
LiteLLM key in sandbox/app logs.

Root causes found during deployment, beyond the original diagnosis:

1. **In OpenHands 1.x the LLM calls are made by the agent-server *sandbox*
   container (on Docker's default bridge), not the app container.** Attaching
   the app to `docker_default` (wq#4007's original plan) is not sufficient —
   a default-bridge container has no route to `docker_default` by name or by
   the `docker_default` subnet IP; `172.17.0.1:4000`, `172.20.0.2:4000`, and
   the `litellm` DNS name all failed from a default-bridge container. Fix:
   LiteLLM now also publishes `172.17.0.1:4000` (docker0 gateway only — LAN
   and tailnet probes against that address still get nothing). This
   deliberately overrides the original "do NOT widen LiteLLM's publish
   address" note, per an explicit Frank session brief naming `172.17.0.1:4000`
   as the fix.
2. The app persists the agent's LLM settings in `settings.json` in
   **plaintext**, rewritten 0644 on every write; env `LLM_*` is not read for
   the agent key. Fix: key set via `POST /api/v1/settings` using
   `agent_settings_diff`/`conversation_settings_diff` payloads (not a full
   overwrite); `/home/lumen/.openhands` is now `root:root 0700` so non-root
   workers on Lumen cannot read it (a bare `chmod` on the file itself does not
   persist across the app's own rewrites — the directory mode is what sticks).
3. The sandbox→app callback (webhooks + the default MCP server) uses
   `host.docker.internal:3000`; binding the app to `127.0.0.1` only broke it
   (`MCPTimeoutError` after 30s in the agent-server). Fix: the app also
   publishes `172.17.0.1:3000`.
4. The hard-coded 15s `STARTUP_GRACE_SECONDS` marked the sandbox `ERROR` under
   load (load average ~22 at deploy time). Fix: `OH_SANDBOX_KIND=
   DockerSandboxServiceInjector` + `OH_SANDBOX_STARTUP_GRACE_SECONDS=90` — the
   env parser silently ignores `OH_SANDBOX_*` unless `_KIND` is also set, so
   grace alone would have been a no-op.
5. The old `openhands` LiteLLM key was burned (wq#4018); rotated to alias
   `openhands-2`.

### Final deployed configuration

- `/home/lumen/containers/openhands/docker-compose.yml`: ports
  `127.0.0.1:3000` + `172.17.0.1:3000` (off LAN/tailnet — reach the UI via
  `ssh -L 3000:127.0.0.1:3000 lumen@100.117.66.102`); networks
  `openhands_default` + `docker_default`; credentials in `llm.env` (0600),
  not inline in compose.
- Model: `openai/deepseek-v4-flash` via `http://172.17.0.1:4000/v1`, key
  alias `openhands-2`.
- `max_iterations` 500 → 100, analytics consent off, proactive conversation
  starters off.
- `agent-server:1.26.0-python` pre-pulled (5.38 GB) — first-task ghcr pull is
  no longer a cold-start risk.

### Re-verified live, this ticket (wq#4149, 2026-10-17, read-only, host-side only — no docker)

```
$ ss -tln | grep -E ':3000|:4000'
LISTEN 172.17.0.1:3000   LISTEN 127.0.0.1:3000
LISTEN 172.17.0.1:4000   LISTEN 127.0.0.1:4000   LISTEN 100.117.66.102:4000   LISTEN [fd7a:...]:4000
```

No `0.0.0.0` or `[::]` binding for port 3000 — the earlier wide-open exposure
is gone. `GET http://127.0.0.1:3000/alive` and
`GET http://172.17.0.1:3000/alive` both return `200 {"status":"ok"}` — a real
liveness probe exists and answers on both published addresses (see §5,
`ops/wq4111-openhands-litellm-production/probe.py`). `GET /api/conversations`
is still SPA-shell `spa_fallback` (same classification wq#4111 recorded at
landing time) — this is a route-ordering quirk in the UI's static-file
mount, not a regression; it does not gate the smoke-tested task-creation
path, which goes through the authenticated API routes the UI's JS calls, not
a bare `GET`.

## 3. What the benchmark number actually measured (unchanged finding, still true)

- The only "T1 PASS" on record (10-14 19:18 PDT) ran OpenHands **0.25.0**
  headless (`ghcr.io/all-hands-ai/openhands:0.25.0`, `--network host`,
  CodeActAgent, `deepseek-v4-flash`) via `openhands-ticket-runner.sh`/
  `run_agent.sh`. It says nothing about the 1.8 container, `deepseek-v4-pro`,
  or the web UI / V1 app-server path this document is about.
- The one full 8-test suite run against the fixed harness (2026-10-12)
  scored **0.716 FAIL_CLOSE** (threshold 0.8): T2 git 0.25, T7 poisoned-repo
  0.6, **T8 autonomous e2e 0.0** — the agent flipped
  `RUNNING`↔`AWAITING_USER_INPUT` for ~50 iterations and wrote nothing. One
  sample per cell; the source doc itself says T7/T8 need 3 runs each before
  quoting a verdict. None of this suite ran against the 1.8 stack —
  `run_agent.sh` still launches 0.25 containers (§4 item 6).

## 4. Still open (wq#4149 scope — tracked per original item number)

| # | Item | Status |
|---|---|---|
| 2 | Run as non-root; fix root-owned workspaces | Open — needs a privileged (docker-capable) lane |
| 3 | Mem/CPU limits on app + sandboxes; `max_num_sandboxes=5`; reap idle/failed sandboxes | Open — see `ops/wq4149-openhands-1-8-hardening/` for the prepared script |
| 4 | Per-run wall-clock + cost caps (virtual key is $10/30d only) | Open — same package |
| 5 | Headless/unattended behaviour: T8's `AWAITING_USER_INPUT` loop; research the 1.x equivalent of `enable_auto_fake_user_response` | **Blocked on prior-art search**: `fleet-search web`/`github` both returned `ok:false` this session — all general web engines suspended/CAPTCHA'd and no `BRAVE_API_KEY` configured (wq#2595, already open, not re-filed). The one 2026-10-16 smoke task ran to completion with `confirmation_mode=False` and `max_iterations=100` without ever hitting `AWAITING_USER_INPUT`, but that is one simple single-step task, not T8's adversarial scenario — not evidence the loop is fixed |
| 6 | Re-run T1–T8 (T7/T8 ×3) against **this** 1.8 stack; `run_agent.sh` still launches 0.25 | Open — needs `/opt/benchmark` + docker, both outside a BUILD worker's reach; queued as a privileged-lane task |
| 7 | `lumen_state` `/alive` probe (not `/api/health`) | **Closed this ticket** — `check_openhands_alive()` added to `ops/wq4111-openhands-litellm-production/probe.py` (see citation below); wiring it into `/home/lumen/minerva/lumen_state.py` itself is outside every `--add-dir` this lane has, left for a privileged lane |
| 8 | Sandbox is root and can reach the unauthenticated app API on `172.17.0.1:3000` — evaluate docker-socket-proxy / auth | Open — recommendation in `ops/wq4149-openhands-1-8-hardening/README.md`; not fleet-verified (blocked by the same search outage as item 5) |
| 9 | SDK 1.27 vs agent-server 1.26 skew | Unresolved but non-blocking so far — the 2026-10-16 smoke task completed cleanly on `agent-server:1.26.0-python` against whatever SDK version `lumen_state` recorded; not root-caused, not re-tested under load |
| 10 | litellm now also publishes `172.17.0.1:4000` — record in network docs | **Closed this ticket** — `research/NETWORK_PREMORTEM.md` S1/S10 updated with live-verified bindings |

Do **not** widen exposure further while closing any of the above — UI stays
`127.0.0.1` + `172.17.0.1` only (ssh-tunnel/compose-bridge reach), never LAN
or tailnet-published.

## 5. Evidence trail

- `ops/wq4111-openhands-litellm-production/probe.py` — unprivileged host-side
  probe (litellm readiness, openhands `/alive`, API-routing classification).
- `ops/wq4149-openhands-1-8-hardening/` — this ticket's prepared
  privileged-lane hardening script + tests (items 2, 3, 4, 8).
- `/home/lumen/kb/ISSUE-LOG.md` — docker/compose access denial to BUILD
  workers is a known, by-design boundary (see existing rows on wq#4007/
  wq#4111/wq#4018's identical finding); not re-litigated here.
