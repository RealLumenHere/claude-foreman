# ISSUE-LOG row #474 — staged for `/home/lumen/kb/ISSUE-LOG.md`

Append this row to `/home/lumen/kb/ISSUE-LOG.md`. It could not be written from this
worker: `guard.sh` denies Write/Edit inside the primary tree `/home/lumen/kb`
("primary repo roots are read-only for workers ... make this change in your own
worktree and land it with lumen-pr", wq#1719/wq#900). That is the same refusal
already recorded as ISSUE-LOG #473, whose precedent is to land the text in the
foreman repo instead — followed here. #473 is the last row in the file, so this is
#474.

```
| 474 | wq#2153 ([ops] isis: retire disable-gpus.service + logind HandlePowerKey=ignore) cannot be executed by a foreman BUILD worker at all: P1 needs `systemctl disable --now disable-gpus.service` + a node restart on isis, P2 needs a root write into `/etc/systemd/logind.conf.d/` — every one of those verbs is hard-denied | `harness/guard.sh:89-94` blocks `sudo\|reboot\|shutdown\|poweroff\|halt` and `systemctl (stop\|disable\|mask)` ANYWHERE in the command string, explicitly including inside `ssh host '...'` quoting (wq#879/wq#1548 closed that evasion on purpose). The broker is no way round it either: this worker's grant is `{node: isis, verbs: [run]}` and `run` on an ssh-class node is READ-ONLY (`fleet-exec-policy.yaml:180-196` run_readonly_cmds); `restart` is not granted and is scoped to `isis: [ollama]` anyway; `sudo` and `power` are unimplemented v2 verbs. There is no in-harness path, so this is a ticket-routing mismatch, not a work defect — an [ops] ticket whose deliverable is privileged remote state was dispatched into a BUILD worktree | did NOT evade the guard and did NOT fake a done. Landed what IS reachable: `ops/wq2153-isis-retire-disable-gpus/` — RUNBOOK.md (live read-only evidence table gathered via `fleet-exec run isis`, exact apply/verify/rollback commands) + the ready-to-install `90-ignore-power-key.conf`. Ticket BLOCKED for a privileged operator. Two real fixes owed: (a) route [ops] tickets needing root-on-node to an ops lane with a privileged path rather than a BUILD worktree; (b) land the broker's v2 `sudo`/`power` verbs (`fleet_exec_policy.check_sudo`/`check_power` already exist, so the policy matrix is provable today — only the CLI wiring is missing) | BLOCKED (capability wall, not a work defect) |
```
