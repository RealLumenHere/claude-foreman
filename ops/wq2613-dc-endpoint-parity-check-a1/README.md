# wq#2613 — DC endpoint parity check (nightly) + FLEET_TOOLS/state entries

Design: `MCP_CONTROL_PLANE_HA_DESIGN.md` §2 ("nightly parity check
(versions/config)"), §3.2/§3.3 (what parity means: versions, dc-enforce-config,
watchdog, unit drop-ins), §3.5 (failback semantics), §3.1 (`GET /pool`).

## What's in this directory

| file | maps to |
|---|---|
| `dc-endpoint-parity-check.py` | the nightly check itself: for anubis/bes/nephthys, ssh's in (BatchMode, `lumen`'s own fleet keys per §3.2) and compares supergateway/DC npm versions, `dc-enforce-config.py` stdout, `mcp-gateway-watchdog.timer` active state, and `dc-gateway-hardening.conf`/`dc-gateway-logging.conf` sha256 against Lumen (pref 1) as baseline. Any mismatch → `aq add dc-parity <node>.<axis> ...` (upserts, so a still-drifted node doesn't spam a new row every night). |
| `dc-endpoint-parity-check.service` / `.timer` | nightly systemd oneshot, `OnCalendar=*-*-* 03:17:00`, `Persistent=true`. |
| `wq2613_fleet_tools.diff` | append to the existing `commander_mcp` group in `context/fleet_tools.yaml` (outside version control, same outside-VCS pattern as wq#1720/wq#2095/wq#2607): the endpoint list in pref order, the `GET /pool` one-liner, and §3.5's failback semantics, condensed. |
| `lumen_state_ingest.sh` | prints the `lumen_state.py ingest mcp_pool mcp_endpoints '<json roster>'` + `state-regen` commands, and the follow-up to hand-apply the fleet_tools diff. |
| `../../docs/fleet/UN_RETIRE_CHECKLIST.md` | new checklist (none existed before this ticket) for bringing a decommissioned node back into service; item 3 is exactly the ticket's ask, "enable node's dc-gateway/cloudflared", cited against the Nephthys root cause in the design doc's §1. |

## Verified this session (read-only, on Lumen itself — the only node this BUILD lane can reach)

- `supergateway` = 3.4.3, `@wonderwhy-er/desktop-commander` = 0.2.46 (both
  `package.json` reads under `/home/lumen/.npm-global/lib/node_modules/`) —
  matches the design doc's pinned versions, so Lumen is a valid parity
  baseline today.
- `mcp-gateway-watchdog.timer`: `LoadState=not-found` on Lumen — the unit is
  not installed here despite `/home/lumen/bin/mcp-gateway-watchdog.sh`
  existing on disk. This is real, current drift the parity check as designed
  would (and should) flag once it's deployed and Lumen itself is brought into
  the same watchdog coverage as the endpoint nodes it's compared against —
  logged as `kb/ISSUE-LOG.md #<see below>`.
- `/home/lumen/bin/dc-enforce-config.py`: `CFG =
  /home/lumen/.claude-server-commander/config.json`, `BLOCKED` = 12 entries
  (dd/mkfs family/wipefs/shred/fdisk family/reboot family/macchanger), stdout
  shape `[dc-enforce:tier1] blockedCommands=<n> onboarding=<bool>` — this is
  the exact string the parity check diffs across nodes.
- `desktop-commander-mcp.service` + its two live drop-ins (`50-CPUWeight.conf`,
  `50-IOWeight.conf` at `CPUWeight=1000`/`IOWeight=1000`, and
  `no-body-logs-journald.conf`) via `systemctl cat` — used to confirm the
  per-node dc-gateway.service shape this ticket's parity check assumes
  (`dc-gateway-hardening.conf`/`dc-gateway-logging.conf`, named per
  wq#2607/wq#2608's units).

## Status: artifacts staged, NOT deployed — same structural gap as wq#2607/wq#2608

This BUILD lane's grant is read-only against Lumen itself; it has no ssh path
to anubis/bes/nephthys (fleet-exec, the sandbox's broker, only allows
`ssh(lumen, run)` for this ticket — confirmed by wq#2607/wq#2608, not
re-tested destructively here) and cannot invoke `lumen_state.py`/
`state-regen`/`systemctl enable` (all "requires approval", confirmed live this
session — a Bash-allowlist gap, not a guard.sh deny). Concretely:

- `dc-endpoint-parity-check.py` is written and reviewed but has only been
  exercised against Lumen's own local checks (version reads, dc-enforce-config
  output, drop-in paths) in this session — the ssh-to-anubis/bes/nephthys path
  is unverified because this lane cannot reach those hosts. The real `lumen`
  account (§3.2's ssh-keys-to-every-node design) can; that's the intended
  runtime, not this sandbox.
- The `.service`/`.timer` are not installed (`systemctl enable/start` need
  root + are outside this lane's grant regardless).
- `wq2613_fleet_tools.diff` needs hand-application to
  `/home/lumen/minerva/context/fleet_tools.yaml` (outside version control,
  outside this worktree's `--add-dir`, wq#1467) by a session with that access.
- `lumen_state_ingest.sh`'s commands need a session whose Bash allowlist
  covers `lumen_state.py`/`state-regen`.

## To actually apply (needs root + fleet-exec grant on anubis/bes/nephthys, or a session running as the real `lumen` account)

```bash
sudo install -m0755 ops/wq2613-dc-endpoint-parity-check-a1/dc-endpoint-parity-check.py /home/lumen/bin/dc-endpoint-parity-check.py
sudo install -m0644 ops/wq2613-dc-endpoint-parity-check-a1/dc-endpoint-parity-check.service /etc/systemd/system/dc-endpoint-parity-check.service
sudo install -m0644 ops/wq2613-dc-endpoint-parity-check-a1/dc-endpoint-parity-check.timer /etc/systemd/system/dc-endpoint-parity-check.timer
sudo systemctl daemon-reload && sudo systemctl enable --now dc-endpoint-parity-check.timer
# apply wq2613_fleet_tools.diff to /home/lumen/minerva/context/fleet_tools.yaml by hand, bump updated:, fleet-tools-render
ops/wq2613-dc-endpoint-parity-check-a1/lumen_state_ingest.sh   # prints the ingest + regen commands
```

## What this ticket actually closes today

- The complete, reviewed nightly parity-check script + timer, content-matched
  against Lumen's real live units/config rather than guessed (see "Verified"
  above), plus one real, currently-true finding (`mcp-gateway-watchdog.timer`
  not installed on Lumen) that the check as designed would surface.
- The `fleet_tools.yaml` `commander_mcp` addition (endpoint roster, `GET
  /pool`, §3.5 failback semantics) as a diff, per the established
  outside-version-control pattern.
- The `mcp_endpoints` roster ingest commands (single subject/predicate holding
  the whole ordered pool, one lookup instead of four joined facts).
- `docs/fleet/UN_RETIRE_CHECKLIST.md` (new — none existed), with the
  "enable node's dc-gateway/cloudflared" item root-caused against the
  Nephthys finding in `MCP_CONTROL_PLANE_HA_DESIGN.md` §1.
- Confirmation, live, that the `lumen_state.py`/`state-regen` Bash-allowlist
  gap wq#2608 found still applies to this lane, and that this lane still has
  no execution path to anubis/bes/nephthys — both logged in
  `kb/ISSUE-LOG.md`, not worked around.
