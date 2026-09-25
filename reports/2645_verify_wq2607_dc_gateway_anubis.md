# wq#2645 — verify-pass: wq#2607 DC endpoint on Anubis: dc-gateway + own cloudflared tunnel + Access app

**Verdict: CONFIRM** — the shipped bundle (`ops/wq2607-dc-endpoint-anubis/`) is exactly what it
claims to be: a complete, content-matched artifact set staged for a live deploy that never
happened, with the deploy blocker (no fleet-exec grant path to Anubis for an install-class verb)
logged accurately and not worked around. Ground-truth probing of Anubis itself (this ticket's own
`ssh(anubis, run)` grant, which wq#2607's `ssh(lumen, run)` grant did not have) confirms the
bundle's central claim: nothing from it is installed.

PR: `http://100.121.17.5:3000/lumen/claude-foreman/pulls/1009` (merged `acc2b26b85` /
`12f01b5c62`, `Refs: wq#2607`). `ops/wq2607-dc-endpoint-anubis/` is the entire diff: `README.md`,
`cloudflared-ingress-anubis.yml`, `dc-enforce-config.py`, `dc-gateway-hardening.conf`,
`dc-gateway-logging.conf`, `dc-gateway.service`, `install.sh`, `mcp-gateway-watchdog.{sh,service,
timer}`, `wq2607_fleet_tools.diff`.

## What was verified and holds up

- **Central honesty claim.** README's `## Status: artifacts staged, NOT deployed` and the wq
  board's own done-notes both say the same thing: nothing was installed, copied, started, or
  requested against Anubis, Cloudflare DNS, or Cloudflare Access. Confirmed independently via
  direct live probes on Anubis (this ticket's `ssh(anubis, run)` grant, none of which wq#2607 had):
  - `systemctl list-unit-files dc-gateway.service` → `0 unit files listed`
  - `systemctl cat dc-gateway` → `No files found for dc-gateway.service.`
  - `systemctl cat cloudflared` → `No files found for cloudflared.service.` (cloudflared itself is
    not even installed on Anubis yet, matching the README's own §3.2 citation: "verified
    2026-09-25 no node, no cloudflared")
  - `systemctl is-active cloudflared` / `mcp-gateway-watchdog.timer` → both `inactive`
  - `ls /home/anubis/bin` → `No such file or directory`
  - `ls /etc/systemd/system/{dc-gateway.service,mcp-gateway-watchdog.timer,cloudflared.service}` →
    all `No such file or directory`
- **`dc-gateway.service` / hardening / logging drop-ins**: content and rationale match Lumen's
  live `desktop-commander-mcp` unit as described (session-timeout-less stateless ExecStart, T1
  hardening `CPUWeight=1000 IOWeight=1000 MemorySwapMax=1G Nice=-5 OOMScoreAdjust=-900` per
  `MCP_CONTROL_PLANE_HA_DESIGN.md` §3.3, journald-avoidance logging drop-in matching
  `no-body-logs-journald.conf`'s rationale). Byte-parity against Lumen's actual live unit could not
  be re-checked this session (this ticket's grant is `ssh(anubis, run)`, not `ssh(lumen, run)` —
  the same node-scoping the author had, just pointed at the other node), but nothing in the
  content is internally inconsistent or suspicious.
- **`dc-enforce-config.py`**: `BLOCKED` list is the standard tier-1 irreversible/host-availability
  set (dd, mkfs\*, wipefs, shred, fdisk/sgdisk/parted, reboot/shutdown/poweroff/halt, macchanger);
  only `CFG` path changed to the anubis home, as claimed. No injected commands, no widened scope.
- **`mcp-gateway-watchdog.sh`**: read in full. Flap-guard math checked by hand —
  `FLAP_THRESHOLD=2` over a 1800s window with the restart itself appended before the count, so a
  3rd restart inside 30 min (not the 2nd) trips `flapping`, matching the comment ">2/30min". Cooldown
  (300s) and 2-consecutive-fail-before-restart logic are correct. `sudo -n systemctl restart
  "$unit"` is only ever fed one of two hardcoded literals (`dc-gateway` or `cloudflared`), matching
  the two-command NOPASSWD sudoers line the README specifies — no injection surface. Alert-on-fail
  is best-effort and never affects the script's exit code, as commented.
- **`cloudflared-ingress-anubis.yml`**: correct hostname/service/catch-all shape, and the
  README correctly calls out the 06-29 "tunnel must be created ON the target node" gotcha rather
  than repeating it.
- **`install.sh --verify` handler exists** (unlike wq#2608/wq#2646's bundle, which shipped without
  one) and follows the `ops/VERIFY_CONTRACT.md` literal-`--verify`-arm-before-install shape
  correctly; it is read-only and does not install.

## Liveness probe (`ops_verified(wq2607-dc-endpoint-anubis)`) — could not run as prescribed, ran the equivalent by hand instead

The ticket's prescribed command, `bash /home/lumen/foreman/ops/wq2607-dc-endpoint-anubis/install.sh
--verify`, is not runnable from this verify lane:

- `bash <path>` is not on `harness/worker-settings.json`'s Bash allowlist (only the unrelated
  `Bash(bash scripts/*)` pattern matches, and `ops/` isn't `scripts/`) — refused
  "This command requires approval", not retryable per doctrine.
- Even granted an exception, the handler's `CTX=/home/lumen/minerva/context` reads
  `fleet_tools.yaml`/`FLEET_TOOLS.md`, both outside this worktree's `--add-dir` grant (`minerva`
  is deliberately excluded from worker grants per wq#1467) — confirmed refused (`grep`/`cat`
  "blocked_path") both this session and the prior killed attempt
  (`/home/lumen/foreman/_runs/wq2645/failure.json`, attempts 1-2, same two paths).

This is a structural sandbox gap identical in class to the one wq#2607's own author hit
(ISSUE-LOG #740) and to wq#2646's Bash-allowlist finding (#764) — not something a redispatch
fixes. Logged as ISSUE-LOG #770.

Rather than retry the blocked shape (doctrine: a blocked call is not retryable), this ticket's own
capability grant is the opposite scope of wq#2607's (`ssh(anubis, run)` vs `ssh(lumen, run)`), so
the meaningful liveness question — "is anything actually running on Anubis" — was answered
directly against the real target instead (see probes above). All read `inactive`/`absent`/`no
files found`, which is the correct, honest answer given the bundle's own claim of non-deployment.

**Design note, not a defect in this diff**: `install.sh --verify` itself checks only two
`vcontains` markers against the Lumen-local `fleet_tools.yaml`/`FLEET_TOOLS.md` inventory docs —
it never calls `vunit_file`/`vactive` against any of the systemd units or the cloudflared ingress
this bundle actually ships for Anubis, unlike `ops/VERIFY_CONTRACT.md`'s own template (built
around exactly `vunit_file`+`venabled`+`vactive` for a units+config bundle). The author is
explicit about this choice and its limit (README: "`install.sh` \| read-only `--verify` of the
`fleet_tools.yaml` edit once it's applied by hand ... installs nothing"; "update that wording once
steps 1-7 above actually land, don't leave it stale") — so a future `VERIFY OK` from this bundle
means "the inventory doc entry was hand-applied," not "the Anubis DC endpoint is live," and the
board's `Liveness: ops_verified(wq2607-dc-endpoint-anubis)` phrasing could read as stronger than
that to anyone who doesn't open the bundle. Not treating this as a contradiction of wq#2607's own
claims — the scope limit is disclosed, not hidden, and matches "What this ticket actually closes
today" in the README exactly — but worth a process note for whoever owns the liveness-gate naming
convention (`harness/liveness.py` / `ops/VERIFY_CONTRACT.md`): a bundle whose only checkable
target is a doc edit and a bundle whose target is a live service probably shouldn't both surface
as an undifferentiated `ops_verified(...)` tag on a verify ticket.

## Disposition

No contradiction found. wq#2607's diff does exactly what its own README and wq-board notes say it
does, the artifact content is sound, and direct live-target probing of Anubis (this ticket's own
grant, which the author's ticket did not have) confirms the "staged, not deployed" claim rather
than contradicting it. Not escalating to opus-5@high.
