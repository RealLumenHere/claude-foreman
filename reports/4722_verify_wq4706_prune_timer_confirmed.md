# wq#4722 — verify-pass on wq#4706 (judgment: gate-snapshot retention landed + scheduled)

Verifier: orchestrator (fresh cold read), 2026-10-28 06:46 UTC. Target PR: #2508 (merge 636f71e4).
Verdict: **CONFIRM**. All six claims in wq#4706 hold against the live box; one observation filed separately.

## Claims checked live (not from the PR text)

| # | wq#4706 claim | Probe | Result |
|---|---|---|---|
| 1 | PR #2473 (2f2c617a) and #2508 (636f71e4) on main | git merge-base --is-ancestor | both on main; def prune_vault at harness/action_gate.py:738 |
| 2 | timer enabled + active, daily ~05:15 | systemctl list-timers / is-enabled / is-active | enabled, active; last 2026-10-28 05:12:19Z, next 2026-10-29 05:16:43Z |
| 3 | timer fires | journalctl -u gate-snapshot-prune.service | fired 05:12:32Z: pruned 38 files older than 30d no secret pattern, clean exit, 12.6s |
| 4 | vault shrinks from 2440 / 1.7G | find / du | 1844 files / 1.6G; 2440 - 576 (first run) - 38 (timer) + new reconciles |
| 5 | install.sh --verify exits 0 | bash ops/wq4706-gate-snapshot-prune-timer/install.sh --verify | VERIFY OK rc=0 (dry-run, read-only) |
| 6 | runs as lumen via action_gate prune-vault | systemctl cat | User=lumen, ExecStart=python3 .../action_gate.py prune-vault |

## Break attempts
- Secret safety: prune_vault unlinks only when old_enough AND NOT _carries_secret_pattern(p) (content read, not filename); symlinks/non-regular skipped. 519 >30d files remain, all secret-bearing, correctly left for secret-backup-sweep. No credential pre-image is prunable at any age.
- Scope: #2508 added only --dry-run; prune_vault not widened (diff-confirmed).
- Scheduler: oneshot under timer + ExecStartPost cron-stamp; a failure would surface in journal+stamp. Fired once cleanly.

## Observation (follow-up, not a contradiction)
Retention fixes the file-count problem, not the bytes problem: 1.2G of 1.6G is <30d old, dominated by a few large recent pre-images (57M TWN4DevPack.zip, 38M trivy report, 36M uv binary), untouchable for 30d by design. A size cap or a skip-snapshot-over-N-MB rule would be a separate ticket, out of scope for wq#4706 which did exactly what wq#4657 said was missing.
