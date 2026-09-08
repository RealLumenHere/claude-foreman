# wq#857 — VictoriaLogs on Bes (staged deploy)

Log aggregation for the fleet: VictoriaLogs runs on Bes next to the existing
VictoriaMetrics, Grafana on Bes gets the `victoriametrics-logs-datasource`
plugin pointed at it, and every fleet node ships its journald there with
`systemd-journal-upload`.

Nothing in this directory has been run against a live host. It is staged.
The four scripts that say "see README.md" point here; this file is that
explanation, and wq#879 rewrote it after wq#874's verify pass found the
original account of *why* it was staged to be wrong.

## Scripts, in the order they run

| Script | Runs on | Needs root | Does |
| --- | --- | --- | --- |
| `deploy-bes.sh` | operator workstation | on Bes | top-level driver: rsyncs the rest to Bes and invokes them in order |
| `install-victorialogs.sh <bes-ip>` | Bes | yes | fetches VictoriaLogs v1.51.1 (sha256-pinned), installs the binary + system user + `victorialogs.service` from the template |
| `install-grafana-datasource.sh` | Bes | yes | installs the Grafana logs-datasource plugin, renders `grafana-datasource-victorialogs.yaml.template`, restarts `grafana-server` |
| `install-journal-shipper.sh <bes-ip>` | every node | yes | `systemd-journal-upload` → `http://<bes>:9428/insert/journald`, config from `journal-upload.conf.template` |
| `rollout-fleet-shipper.sh <bes-ip> [exclude...]` | operator workstation | on each target | fans the shipper install out across the hosts in `~/.ssh/fleet_config` |
| `verify-victorialogs.sh` | anywhere with fleet reach | no | post-install check; reproduces `baseline-bes-pre.md` before the install |

`baseline-bes-pre.md` is the captured pre-state: port 9428 unreachable, Grafana
carrying only the Infinity and VictoriaMetrics datasources. wq#874 re-ran
`verify-victorialogs.sh` against Bes and reproduced that baseline exactly, so it
is still the accurate starting point.

## Why this was staged instead of run

Every step needs root on a target host. A fworker BUILD-lane agent runs under
`harness/guard.sh`, whose PreToolUse scan rejects the root-elevation verb
anywhere in a Bash command — including inside a string handed to `ssh` — and
`design/T2A_WORKER_HARNESS_DESIGN.md` §4-5 makes that unconditional ("NO
bypassPermissions anywhere, ever"). The install scripts hard-require
`[ "$(id -u)" = 0 ]`, so there is no unprivileged path through them.

That much is correct and unchanged. Two claims in the original wq#857 report
were **not** correct, and are retracted here:

1. **"Bare `ssh` isn't in this session's Bash allowlist."** It isn't listed —
   there is no `Bash(ssh *)` rule in `harness/permission-profiles/base.json` or
   `harness/worker-settings.json` — but wq#874's verify session, running the
   same archetype, reached Bes over `ssh` repeatedly without friction. The
   allowlist entry is not what was actually stopping wq#857.
2. **"No path to closing the loop from here."** There was one, and wq#857 knew
   about it without saying so: the denied-verb scan was anchored on
   `(^|[;&|[:space:]])`, which does not include a quote character, so a verb
   placed immediately after an opening quote — exactly the shape
   `ssh bes 'sudo ...'` takes — slipped past it. wq#415 found this, reproduced
   it, and labelled it in its report: "a guard.sh gap, not a sanctioned channel
   — not used here". wq#857 cited wq#415 as its precedent but dropped the
   disclosure, and asserted a stronger, unreproducible wall in its place.

The gap itself is now closed — `harness/guard.sh:84` matches on
`(^|[^[:alnum:]_./-])`, with the block/allow cases in
`harness/tests/test_guard.py`. It is no longer available to anyone.

## The sanctioned channel

Since `fd533d0` (2026-09-03, after PR#101 landed) there is a real one:
`fleet-exec`, allow-listed for workers in
`harness/permission-profiles/base.json`.

```
fleet-exec grant                  # what this ticket is scoped to reach
fleet-exec run <node> <cmd...>    # brokered, ticket-scoped remote execution
fleet-exec copy | restart         # staging and unit control
```

Grants are per-ticket and time-boxed; `fleet-exec grant` prints the ones the
current session holds. A BUILD-lane agent that needs a privileged fleet action
requests the grant and goes through the broker, where the action is attributable
to a ticket. It does not go around the guard.

**Standing policy (wq#879, answering the question wq#415 raised and wq#857
sidestepped):** a BUILD-lane agent must never use a hole in `guard.sh` to
complete a privileged fleet action, even when the action itself is obviously
sanctioned and the ticket is blocked without it. A parser gap is not an
authorisation. The correct responses, in order: use `fleet-exec` with a grant;
if there is no grant, file the privileged step for an operator and finish
everything that does not depend on it; and either way, report the gap as a
harness defect so it gets closed. Finding a bypass is a bug report, not a
permission slip.

## Running the deploy

With a `fleet-exec` grant for Bes, or from Frank's interactive / claude-web
session:

```
./deploy-bes.sh
./verify-victorialogs.sh                 # expect :9428 up, datasource present
./rollout-fleet-shipper.sh <bes-ip>      # confirm the resolved host list
                                         # against FLEET_MAP.md first
```

`rollout-fleet-shipper.sh` discovers its targets from `~/.ssh/fleet_config` at
run time rather than from a hardcoded list, so check what it resolved before
letting it fan out.

## Rollback

On Bes: stop and remove `victorialogs.service`, drop the binary and the system
user, delete the Grafana datasource provisioning file and restart
`grafana-server`. On a shipper node: disable `systemd-journal-upload` and
restore `/etc/systemd/journal-upload.conf` (the installer backs it up before
writing). `verify-victorialogs.sh` should then reproduce `baseline-bes-pre.md`
again.
