# wq#4198: OpenHands sandbox egress allowlist + resource limits

Finding (openhands-prod-5de914, 2026-10-18): `oh-agent-server-<id>` sandboxes
run on the default bridge (docker0) with no mem/cpu/pids limits, and a
docker0 container can open TCP to host sshd (172.17.0.1:22, 192.168.8.160:22),
Horus :22, Ma'at :22, the internet, and the unauthenticated app API
172.17.0.1:3000.

This package is **prepared and unit-tested, not applied**. Docker (beyond
`ps`/`logs`), sudo, and `ssh … sudo` are all denied to the fworker BUILD lane
by `foreman/harness/guard.sh`, the same wall wq#4152 hit. Apply it from a
root, docker-capable lane using the steps under Apply.

## Research: what OpenHands 1.8 can do itself

`fleet-search web` and `github` both failed this session (every SearXNG
engine suspended or CAPTCHA'd, no `BRAVE_API_KEY`, DeepSeek balance 402,
GitHub 401; wq#2595). So the source was read directly, pinned to tag
`1.8.0` of `github.com/OpenHands/OpenHands`
(`openhands/app_server/sandbox/docker_sandbox_service.py`, 714 lines):

| Line(s) | What it says |
|---|---|
| 578-670 | `DockerSandboxServiceInjector` fields: `container_url_pattern`, `host_port`, `max_num_sandboxes` (default 5, 599-602), `mounts`, `exposed_ports`, `health_check_path`, `extra_hosts` (default `host.docker.internal: host-gateway`), `startup_grace_seconds`, `use_host_network`, `kvm_enabled`. **No memory, CPU, pids, network-name or extra-run-kwargs field.** |
| 469 | `network_mode = 'host' if self.use_host_network else None`: default bridge or host, nothing else |
| 484-506 | `containers.run(image, command, name, environment, ports, volumes, working_dir, labels, detach, init, extra_hosts, network_mode, devices)`: no `mem_limit`/`nano_cpus`/`pids_limit`/`network` |
| 598 | `container_name_prefix = 'oh-agent-server-'` |

`openhands/app_server/config.py:287` (same tag) builds config with
`from_env(AppServerConfig, 'OH')`, so injector fields map to
`OH_SANDBOX_<FIELD>` once `OH_SANDBOX_KIND` is set. That is the pattern
`OH_SANDBOX_STARTUP_GRACE_SECONDS` already uses live
(`research/OPENHANDS_1_8_READINESS.md` §2 item 4). For wq#4152 item 3 that
makes the knob `OH_SANDBOX_MAX_NUM_SANDBOXES`, and 5 is already the default.

**Answer to scope item 1:** the injector cannot apply resource limits or a
dedicated network in 1.8.0. `use_host_network=true` would widen exposure,
not narrow it. Both controls have to live outside OpenHands, which is what
this package does. One caveat: the running image is tagged `openhands:1.8`.
Apply step 0 checks that it really carries this source.

## Design

`sandbox_guard.py` runs as root in `lumen-oh-sandbox-guard.service`:

- **Fail-closed egress.** `INPUT` and `DOCKER-USER` both send `-i docker0`
  to `OH-SBX-SRC`. Each running docker0 container that is *not* named
  `oh-agent-server-*` gets a per-IP `RETURN` there, so its behaviour is
  unchanged. Everything else falls through to `OH-SBX-EGRESS`, including a
  sandbox in its first millisecond and any docker0 container the watcher
  hasn't seen yet. `OH-SBX-EGRESS` returns replies plus new TCP whose
  *original* destination is 172.17.0.1:4000 (LiteLLM) or 172.17.0.1:3000
  (webhook/MCP callback), then logs (rate-limited `oh-sbx-deny:`) and rejects
  everything else. The rules only `RETURN` or `REJECT`, never `ACCEPT`, so
  nothing becomes reachable that wasn't before (`test_never_accepts`).
- **Why INPUT as well as DOCKER-USER.** 172.17.0.1 and 192.168.8.160 are host
  addresses, so that traffic terminates in `INPUT` and never crosses
  `FORWARD`/`DOCKER-USER`. A DOCKER-USER-only allowlist would leave host sshd
  open. `--ctorigdst` matches whether a published port is served by
  docker-proxy (`INPUT`, no DNAT) or by DNAT (`FORWARD`, rewritten
  destination).
- **Why not a user-defined network.** The injector hard-codes the default
  bridge (line 469). Moving a sandbox after start would mean re-homing the
  published agent-server port the app calls back on, which is untested. The
  per-bridge rules need no OpenHands change.
- **Boot.** The unit is ordered `Before=docker.service`. Its `start` mode
  drops every exemption restored from `rules.v4`, because docker can hand
  those stale IPs to a sandbox. The watcher then rebuilds the exemptions once
  docker is up. On a restart with docker already running, `start` reconciles
  in place instead, so egress doesn't blip.
- **Limits.** Any running sandbox with `Memory`/`NanoCpus`/`PidsLimit` unset
  gets `docker update --memory 6g --memory-swap 6g --cpus 2 --pids-limit 2048`.
  Sizing: the host has 4 CPUs and 60 GB, and at the default 5 sandboxes the
  worst case is 30 GB. The update runs on the container-start event, so a
  sandbox runs unlimited for the first second or so. Limits survive
  stop/start of the same container.
- **Drift guard (wq#2819).** The per-IP `-A OH-SBX-SRC -s … -j RETURN` lines
  change on every container start, so `iptables_drift_guard.py` now ignores
  exactly those lines and nothing else. Losing an `OH-SBX-EGRESS` rule or the
  `OH-SBX-SRC` tail jump is still drift. Fixture:
  `../wq2819-iptables-drift-guard/fixtures/wq4198_sandbox_guard.rules`.

### Accepted costs (from the ticket's scope)

- Sandboxes get **no DNS and no internet**. A task that `git clone`s from
  GitHub or runs `pip install` inside the sandbox will fail. The ticket's
  allowlist is LiteLLM plus the callback, nothing more. If repo-clone tasks
  are needed later, add a filtering proxy as its own ticket. Don't widen this
  allowlist.
- 172.17.0.1:3000 stays reachable because the callback requires it, so the
  unauthenticated app API is still exposed to the sandbox. Fixing that is
  wq#4152 item 8 (auth or socket proxy), and this package doesn't duplicate
  it.

### Coordination with wq#4152

- Item 3: sandbox limits are covered here. wq#4152 keeps the app-container
  limits from `harden_app.py`. Its `max_num_sandboxes` knob is
  `OH_SANDBOX_MAX_NUM_SANDBOXES`, default 5 (see Research).
- Item 8: untouched.

## Apply (root, docker-capable lane; no reboot; never Thor)

0. Confirm the source matches the image:
   `docker exec openhands sh -c 'grep -n "network_mode\|mem_limit\|containers.run" $(python3 -c "import openhands.app_server.sandbox.docker_sandbox_service as m; print(m.__file__)")'`.
   Expect `network_mode = 'host' if … else None` and no `mem_limit`. If
   1.8.x grew a limits or network field, prefer it and drop the limits half
   of this package.
1. Baseline. Run a real task with the ticket's driver `oh_run_task.py` (it is
   not in any tree a BUILD lane can read; wq#4007's smoke task was "create
   /tmp/hello.txt containing 'OpenHands works'"). Record
   `execution_status=finished`. Then `bash egress_probe.sh <agent-server image> before`
   (image name from `docker images | grep agent-server`). Expect
   `PROBE before: matrix matched`, meaning everything is open. That shows the
   probe can see reach before you trust it to see none.
2. `systemd-run --unit=wq4198-apply --collect bash /home/lumen/foreman/ops/wq4198-openhands-sandbox-egress/install.sh`
   and follow it with `journalctl -u wq4198-apply -f`. The install backs up
   `/etc/iptables/rules.v4` to `rules.v4.wq4198.<ts>` and the live ruleset to
   `live.wq4198.<ts>.v4`, test-parses the rules (`iptables-restore --test`),
   aborts on global IPv6 on docker0, installs the drift-guard update,
   reconciles once before enabling the unit, and saves.
3. Run `bash egress_probe.sh <image> after`. Expect 4000/3000 `OPEN` and the
   rest `CLOSED`.
4. Run the same real task again. Expect `execution_status=finished`, 0
   `MCPTimeoutError` in `docker logs <sandbox>`, and
   `docker inspect <sandbox> --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}'`
   = `6442450944 2000000000 2048`. Run `journalctl -k | grep oh-sbx-deny` to
   see what the sandbox tried and was refused.
5. `bash install.sh --verify`, then
   `python3 /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py run --dry-run`.
   Expect `OK`.
6. If step 3 or 4 fails: `bash rollback.sh <ts>`.

## Tests (run here, 2026-10-18)

```
python3 -I ops/wq4198-openhands-sandbox-egress/test_sandbox_guard.py      # 14 OK
python3 -I ops/wq2819-iptables-drift-guard/test_iptables_drift_guard.py   # 12 OK (8 prior + 4 new)
```

Both suites are checked by mutation. Adding 172.17.0.1:22 to `ALLOW` fails
`test_sandbox_matrix` and `test_allowlist_is_exactly_the_ticket_pair`.
Disabling the drift guard's `_DYNAMIC_RE` fails
`test_exemption_churn_is_not_drift`. The matrix test walks the emitted rule
strings for the ticket's measured targets. The Horus/Ma'at LAN IPs come from
`lumen_state.py current horus|maat` (`192.168.8.231`, `192.168.8.202`).
