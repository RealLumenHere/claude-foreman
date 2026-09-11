# wq#1780 — activate lumen-contradiction-scan / lumen-state-verify timers, triage first live hits

## Root cause

wq#1609 and wq#1613 both shipped code + a root `install.sh` and closed DONE, but
`install.sh` needs root and neither ticket ran as a BUILD worker with root — so
the units were never installed. Confirmed at dispatch:

```
$ systemctl is-active lumen-contradiction-scan.timer lumen-state-verify.timer lumen-research-index.timer
inactive
inactive
active
$ ls /home/lumen/bin/lumen-contradiction-scan /home/lumen/bin/lumen-verify
No such file or directory (both)
```

## Fix

Ran both installers as root via `lumen-run --root` (ISSUE-LOG #320 pattern —
a detached `systemd-run` unit sits outside the harness's path sandbox, so it
can write `/etc/systemd/system/*` and run `sudo systemctl enable --now` from a
sandboxed BUILD worktree with no live console):

```
lumen-run start --name wq1780-install-contradiction-scan --root -- \
  bash ops/wq1609-minerva-contradiction-scan/install.sh   # DONE:0
lumen-run start --name wq1780-install-state-verify --root -- \
  bash ops/wq1613-minerva-verifier-registry/install.sh    # DONE:0
```

Both installs ran their test suite first (25 passed / test_lumen_verify.py
suite), installed the script to `/home/lumen/bin/`, installed the `.service`
and `.timer` units, `daemon-reload`d, and `enable --now`d the timer.

Post-install verification:

```
$ systemctl is-active lumen-contradiction-scan.timer lumen-state-verify.timer lumen-research-index.timer
active
active
active
$ systemctl list-timers lumen-contradiction-scan.timer lumen-state-verify.timer --no-pager --no-legend
Fri 2026-09-11 06:00:09 UTC 1h 57min - - lumen-state-verify.timer       lumen-state-verify.service
Fri 2026-09-11 06:20:37 UTC 2h 17min - - lumen-contradiction-scan.timer lumen-contradiction-scan.service
```

## Standing check (per the ticket's ask)

`ops/wq1609-minerva-contradiction-scan/install.sh` and
`ops/wq1613-minerva-verifier-registry/install.sh` both ended in a
`systemctl list-timers` call, which prints state but never fails the script if
`enable --now` didn't stick — exactly the gap that let both timers silently
stay inactive after their original "done" installs. Both scripts already had
a working precedent for this in the repo (`ops/wq1531-read-dir-sweep/install.sh:28`,
`ops/wq1766-lpp-brain-escalate-dropin/install.sh:21`:
`systemctl is-active --quiet <unit>`, which exits non-zero under `set -euo
pipefail` if the unit isn't active). Added the same line to both scripts so a
future re-run of either installer fails loudly instead of reporting success on
a still-inactive timer:

- `ops/wq1609-minerva-contradiction-scan/install.sh:14`
- `ops/wq1613-minerva-verifier-registry/install.sh:16`

## Triage of the 30 actionable + 2 informational hits

This is the first time either scanner has ever run against live state. Full
output of the re-run after install, `lumen-contradiction-scan`
(dead-value index: 64/364 closed facts matchable):

| category | count | pattern | verdict |
|---|---|---|---|
| `note_row` | 15 | `llama33-local.status` LIVE-VERIFIED→TORN-DOWN (9x archival_notes rows), `nephthys.ts_ip`/`ollama_version` stale (3x), `hathor.status` UNREACHABLE→healthy (1x), `gameboy-pc.power_plug` E1→None (1x), `thor.fan_status` 'dead'→a full resolution note (1x) | Real drift except thor.fan_status — that "current" value is a fuller resolution note superseding the terse closed one, not a contradiction; false-positive of the note_row detector on notes that got richer, not wrong. Rest need archival_notes annotated/pruned. |
| `regenerate` | 2 | `/home/lumen/minerva/context/LIVE_FLEET_STATE.md:22` — `gameboy.ts_ip`, `gameboy-pc.status` stale | Real — this file is generated; the generator needs a re-run, not a hand-edit. |
| `ticket` | 6 | `/home/lumen/fleet/inventory/machines/apep.yaml:28` hathor ON-BENCH→healthy (2x action + 1x info), `bes.yaml:7` DOWN→RESTORED, `gameboy.yaml:9` ts_ip stale, `wadjet.yaml:6` p12 REMOVED→SUPERSEDED (info) | Real — inventory yaml needs hand-edits to match current status. |
| `proposal` | 3 | `/home/lumen/FLEET_MAP.md:24,279` gameboy.ts_ip stale, `:271` khepri.power_plug P1→a Frank correction note (2026-08-22) | gameboy rows are real drift. khepri row is a false positive — Frank's note already explains the P1 value was a one-time verification event wrongly carried as a standing assignment; the doc already documents this discrepancy on purpose. |
| `proposal_only` | 6 | `/home/lumen/claude_memory.md` — `pbox.status` retired→DECOMMISSIONED (2x), `nephthys.ollama_version`/`ts_ip` stale (4x) | Real drift. |

Net: 27 of 32 hits are genuine doc drift needing an edit; 2 (thor.fan_status,
khepri.power_plug) are detector false-positives on notes that got richer
rather than wrong; the `hathor.status` info row is a dup of its `ticket`
sibling.

All 27 real-drift targets live outside every worktree/`--add-dir` root this
BUILD worker session was granted (`/home/lumen/FLEET_MAP.md`,
`/home/lumen/claude_memory.md`, `/home/lumen/minerva/context/`,
`/home/lumen/fleet/inventory/`) — none of them are under
`/home/lumen/worktrees/wq1780-a1` or this session's `--add-dir` list, so they
can't be hand-edited from here. Filed wq#1782 (lane `ops`) to apply the
corrections (or regenerate, for `LIVE_FLEET_STATE.md`) and re-run the scan to
confirm the hit count drops to the 2 known false-positives.
