# wq#3652 step 2 (wq#3704) — MeshCommander on Thor: privileged-session runbook

## Why no fleet tooling can run this

- This BUILD worker's own sandbox allowlist only permits the container
  runtime's read-only verbs: `harness/worker-settings.json:23-24` allow
  `Bash(docker ps*)` and `Bash(docker logs*)` — nothing that creates or
  mutates a container.
- fleet-exec can't pick up the slack: `harness/fleet-exec-policy.yaml:15-19`
  puts `thor` in the `never.nodes` list, which blocks every verb except
  read-only `run`/`copy` on Thor regardless of any `needs:` grant a ticket
  carries. `THOR_HARDLOCK` enforces the same rule in `fleet_exec_policy.py`
  (comment at `fleet-exec-policy.yaml:7-8`), so this is not an allowlist gap
  to widen — it's deliberate, per instinct tier 0 #1 (Thor is the only
  never-touch node).
- Starting a container does not restart, reboot, suspend, or otherwise
  interrupt Thor itself, so it does not trip the Thor hardlock rule on its
  own merits either — the blocker is purely that no headless session is
  permitted to execute it, not that the action is unsafe to Thor.

Net: this step needs a human (or an interactively-authenticated session) at
a real shell on Thor. The rest of this doc is the checklist for that
session so it doesn't have to re-derive any of the above or guess a flag.

## Pre-flight checks (run on Thor, in the privileged session)

1. Confirm no stale container already holds the name or port:
   `docker ps -a --filter name=meshcommander`
2. Confirm port 3000 is free: `ss -ltnp | grep :3000` (expect no output).
3. Confirm the container runtime is up: `systemctl status docker --no-pager`.

## The command

MeshCommander's web UI is a vPro/AMT remote-management console — the same
class of exposure as a credential (instinct 4). Bind it to Thor's Tailscale
address only, not the inline `-p 3000:3000` form that defaults to all
interfaces:

```
docker run -d --name meshcommander --restart unless-stopped \
  -p 100.112.151.100:3000:3000 ylianst/meshcommander
```

(100.112.151.100 is Thor's Tailscale IP — CLAUDE.md instinct 2.) If the
operator has a reason to prefer the all-interfaces form already recorded in
`README.md:76-78`, that's their call to make at the shell; this is the
safer default absent one.

## Verification

1. `docker ps --filter name=meshcommander --format '{{.Status}}'` — expect
   `Up ...`.
2. `docker logs meshcommander --tail 50` — expect no crash/traceback.
3. From a host on the tailnet: `curl -sf -o /dev/null -w '%{http_code}\n' http://100.112.151.100:3000/` —
   expect `200`.

Once verified, note the done state back on wq#3704 (or its successor
ticket, if this runbook is executed outside the ticket that authored it)
so wq#3652's provisioning chain reflects step 2 as actually complete, not
just queued.
