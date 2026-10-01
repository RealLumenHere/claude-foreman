# wq#3300 — port wq#1698/wq#2546 body-log redaction to bes-mcp-failover

## Problem (ticket, measured)

`bes-mcp-failover.service` (Bes's supergateway+desktop-commander gateway on
:3100/mcp, `lumen_state` `bes.mcp_failover`) has no drop-ins at all: every
MCP request/response body lands in Bes's local journal, same root cause as
wq#1698 on Lumen (`ops/wq1698-supergateway-log-redaction/README.md` — no
`--logLevel` flag set, supergateway defaults to `info`, which logs full
bodies at `dist/gateways/stdioToStatelessStreamableHttp.js:194/207/235/244`).
Measured: 3137 body lines/24h, a fleet sudo password string appearing 9x/24h
in those bodies, journal at 3.9G.

Lumen already carries the fix in two layers:
- `no-body-logs-journald.conf` (wq#1698) — splits stdout (bodies) from
  stderr (errors), routing stdout to a local 0600 file instead of journald.
- `wq2546-mask-body-log.conf` (wq#2546) — wraps the unit's ExecStart so that
  local file receives masked output, closing the window between a secret
  printing and the next `mask_at_rest.py` sweep.
- `/home/lumen/bin/mask-secrets` — the masking pipe both of the above use,
  self-contained bash, portable by `scp`.

This directory ports the same three pieces to `bes-mcp-failover.service`.

## What's here

- `no-body-logs-journald.conf` — drop-in for
  `/etc/systemd/system/bes-mcp-failover.service.d/`, same structure as
  wq#1698's, paths adjusted for Bes's home dir (`/home/bes`, not
  `/home/lumen`) and unit name.
- `bes-mcp-failover-logrotate.conf` — port of wq#1698/wq#2606's logrotate
  drop-in, same caps (50M x 3, compress, copytruncate), owner `bes:bes`.
- `apply.sh` — installs the above, restarts the unit once (detached
  `systemd-run`, never foreground), health-checks `/health`. Mirrors
  wq#1698's `apply.sh` and wq#3092's Bes-targeting ssh pattern
  (`ops/wq3092-bes-3100-tailnet-drop/apply.sh`).
- `apply-mask.sh` — ports mask-secrets onto Bes (`scp` +
  `/usr/local/bin/mask-secrets`) and installs a drop-in that wraps the
  gateway's stdout through it. **Differs from wq#2546's drop-in on one
  point on purpose**: wq#2546 hardcodes the exact Lumen ExecStart as a
  literal in the `.conf` file; this script instead reads
  `systemctl show -p ExecStart bes-mcp-failover.service` at apply time and
  rebuilds the wrapper from the live command. This worker's sandbox has no
  ssh/sudo path to Bes to read that line and verify a hand-written guess
  against it, and the CLAUDE.md doctrine is explicit: never guess a config
  value, verify it. Deriving it live is also strictly safer than a baked-in
  literal — it can't silently drift from the real unit the way wq#2546's
  copy would if Bes's flags ever change.

## Why this worker did not run `apply.sh`/`apply-mask.sh`

Same posture as wq#1698/wq#2546 (`README.md`'s wq#2546 section) and
ISSUE-LOG #68: this worker's sandbox has no `ssh` allow-rule
(`harness/worker-settings.json`) and `Bash(sudo *)` is explicitly denied, so
it cannot reach Bes as root to install a unit drop-in or restart a service.
`fleet-exec run bes ...` was also tried and denied
(`grant:grant_missing_env` — no `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA`
provisioned for this ticket's worker session; confirmed via
`fleet-exec grant`, see `events/fleet-exec.jsonl` entry for
`wq3300`/`bes-mcp-failover.service`). Both scripts are proposals for an
operator or worker session with that access, exactly like `wq829-fast-stop`
and wq#1698/wq#2546 before them.

## Remaining ticket steps (also blocked on the same access gap)

1. Run `apply.sh` then `apply-mask.sh` from an authorized session.
2. `journalctl --vacuum-size=200M` on Bes (or `--vacuum-time=7d`) to reclaim
   the existing 3.9G journal — a one-shot cleanup, not a drop-in; needs the
   same root access.
3. Rotate the fleet sudo password (pending since 2026-07-11, per ticket).
   This is the one step that is a genuine Frank-carve-out under the
   full-autonomy instinct: minting/rotating a credential on a personal/
   fleet-owner account is a physical act nobody but an authorized operator
   can perform. Queuing it here as a physical task, not a gate blocking the
   rest of this ticket's file changes.
