# wq#2178 — apply the wq#2166 gpu-lowpower bundle on hathor (and upgrade nephthys)

**Outcome: BLOCKED.** No sanctioned path exists from a build worker to write to hathor or
nephthys. The drift the ticket describes is still live and was re-confirmed here; nothing
was faked and nothing was applied.

## Drift re-confirmed (2026-09-20, from Lumen, read-only)

Using ticket 2178's `ssh/run` grant via `fleet-exec`:

| probe | result |
|---|---|
| `fleet-exec run hathor cat /sys/class/drm/card1/device/power_dpm_force_performance_level` | `auto` |
| `fleet-exec run hathor cat /sys/class/drm/card2/device/power_dpm_force_performance_level` | `auto` |
| `fleet-exec run hathor systemctl show gpu-lowpower.service -p ActiveState -p ExecMainStatus` | `ActiveState=active` / `ExecMainStatus=0` |

Both D300s at `auto` while the unit reports success — the exact false-green wq#2166 was
built to make impossible (`reports/2166_gpu_lowpower_ordering.md`). `ops-deploy-drift`
should keep its `ops-drift:wq2166-gpu-lowpower-ordering` row open; that alarm is correct.

`fleet-exec run nephthys hostname` returns `nephthys`, so nephthys is reachable read-only
too — but it needs the same bundle write, which is equally unavailable.

## Every candidate write path, and how it fails

The apply is two commands (`ops/wq2166-gpu-lowpower-ordering/RUNBOOK.md`, §Apply): `scp -r`
the bundle to the node, then `ssh <node> 'sudo bash .../install.sh'`. Both halves are
unavailable, and so is the read-only verification step.

| path | attempted | result |
|---|---|---|
| `ssh -o BatchMode=yes -o ConnectTimeout=8 hathor hostname` | yes | `This command requires approval` — `ssh` is absent from `harness/worker-settings.json`'s per-command allowlist (lines 10-100; no `ssh` or `scp` entry) |
| `sudo ...` in any form | not attempted | `Bash(sudo *)` is on the explicit deny list, `harness/worker-settings.json:118` |
| `bash ops/wq2166-gpu-lowpower-ordering/install.sh --verify` (read-only) | not attempted | only `Bash(bash scripts/*)` is allowed (line 93); `bash ops/*` is not. The script also shells out to `ssh` internally, so it would fail on the same missing grant |
| `fleet-exec copy hathor <file> /home/hathor/lumen-drop` | yes | `DENY [grant:no_copy_dir]: no copy grant for hathor:/home/hathor/lumen-drop` — unchanged from what the wq#2166 worker hit |
| `fleet-exec` sudo/privileged verb | n/a | does not exist. `fleet-exec --help` lists `{run,copy,restart,grant}` only; `harness/fleet-exec-policy.yaml:205` documents `sudo_cmds` as "v2 — this is the allowlist `check_sudo()` will use **once fleet-exec grows a `sudo` verb**" |
| `fleet-exec restart hathor gpu-lowpower.service` (interim mitigation only — would not install the bundle) | yes | `DENY [grant:no_restart_unit]: no restart grant for hathor:gpu-lowpower.service`. hathor is deliberately absent from `allow.restart` (`fleet-exec-policy.yaml:165-168`) |

This ticket's actual grant, from `fleet-exec grant`:

```
{"ticket": 2178, "grants": [{"kind":"ssh","node":"hathor","verbs":["run"]},
                            {"kind":"ssh","node":"nephthys","verbs":["run"]}]}
```

`run` is the read-only probe verb (`fleet-exec-policy.yaml:180` `run_readonly_cmds` —
`cat`, `ls`, `stat`, `systemctl show`, `journalctl`, …). There is no verb in the tool that
writes a file or elevates.

### Not attempted on purpose

`ssh` could be reached by wrapping it in an allowlisted interpreter (`python3 …` /
`lumen-run start -- ssh …`), because the allowlist only inspects the top-level command
line. That is deliberate evasion of an explicit refusal — and the thing it would run is
`sudo`, which is hard-denied — so it was not done. Per the worker rules, the refused shape
is named above instead.

## What unblocks this

Any one of:

1. **Grant-side:** a `copy` grant for `hathor:/home/hathor/lumen-drop` and the equivalent
   for nephthys, **plus** an implemented `fleet-exec sudo` verb whose `sudo_cmds` covers
   `bash <dst>/install.sh`. The `copy` grant alone is not enough — staging the bundle
   without a way to run the installer as root changes nothing.
2. **Allowlist-side:** `Bash(ssh *)` and `Bash(scp *)` on the worker allowlist with a
   carve-out for the single `sudo bash .../install.sh` invocation. Note `install.sh`
   already refuses to run as non-root, refuses a node exposing fewer than
   `EXPECTED_CARDS` knobs, and re-reads every card before exiting 0 — the safety is in the
   script, not in the absence of a path to it.
3. **Operator-side:** run the two RUNBOOK §Apply commands by hand on hathor and nephthys,
   then `bash ops/wq2166-gpu-lowpower-ordering/install.sh --verify` from Lumen. That is
   the fastest route and needs no policy change.

Option 3 is the recommendation if this is a one-off; option 1 if more root-on-node ops
tickets are coming, since wq#2166 will not be the last bundle that has to land on a node.

Note also that `fix/wq2166-ops-hathor-gpu-lowpower-service-reports--a1` is **not merged to
`main`** as of `ead56d7`, so the bundle to be applied currently exists only on that branch.

## ISSUE-LOG row

`Write`/`Edit` into `/home/lumen/kb` is guard.sh-blocked for workers (wq#1719/wq#900,
previously recorded as ISSUE-LOG #473), so the row below could not be appended directly.
It is row **476** and should be appended to `/home/lumen/kb/ISSUE-LOG.md`:

```
| 476 | wq#2178 (apply the wq#2166 gpu-lowpower bundle on hathor + nephthys): no sanctioned path exists to write to either node. `ssh`/`scp` are absent from harness/worker-settings.json allowlist ("This command requires approval"); `sudo *` is on the explicit deny list (line 118); `bash ops/*` is not allowed either, so even the read-only `install.sh --verify` cannot be run. fleet-exec: ticket 2178's grant is ssh/run only on both nodes (`fleet-exec grant`), `copy hathor ... /home/hathor/lumen-drop` -> `DENY [grant:no_copy_dir]`, `restart hathor gpu-lowpower.service` -> `DENY [grant:no_restart_unit]`, and there is no sudo verb at all (fleet-exec-policy.yaml:205 `sudo_cmds` is a v2 allowlist for an unimplemented verb). | wq#2166 built a root-only installer and wq#2178 was dispatched to a build worker whose sandbox is read-only against the fleet; the ticket's own "Needs a root-capable path to hathor/nephthys plus copy(hathor)" was a prerequisite, not something the worker could provision. Same structural class as #32. | Drift re-confirmed live from Lumen via the read-only grant: hathor card1 and card2 both `auto`, `gpu-lowpower.service` ActiveState=active ExecMainStatus=0 — the exact false-green wq#2166 fixed. Ticket blocked, not faked; evidence in reports/2178_gpu_lowpower_apply_blocked.md. Unblock needs ONE of: (a) `copy` grant for hathor+nephthys plus an implemented fleet-exec `sudo` verb covering `bash <dst>/install.sh`, or (b) `Bash(ssh *)`/`Bash(scp *)` on the worker allowlist with a sudo carve-out, or (c) an operator running the two RUNBOOK commands by hand. | BLOCKED |
```
