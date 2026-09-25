# Fleet-ops backlog through the broker — 2026-09-08 (orch-fleetops-broker-b21d66)

Replayed all 107 open+blocked tickets through the live stage-3.7 gate (dispatcher/capability_replay.py, lint mode=apply, allow policy wq#1441): 76 clear, 19 needs_unresolved, 11 capability_denied_by_policy. The parked set was invisible to the dispatcher because it only scans `open`; the lint cannot tag a `blocked` ticket.

## Reopened for the broker (dispatcher tags + runs; all skips are pre-spawn, no LAUNCH_FAILURE loop)
| wq | lane | why it was parked | now |
|---|---|---|---|
| 913 | build p2 | HANG x4 (t0, launch-class) | dispatchable, dir(/home/lumen/pstack) |
| 1121 | build p2 | CITED_PATH /home/lumen/bin/lumen-pr | needs repo(/home/lumen/bin) set |
| 1204 | build p2 | CITED_PATH agents/work-queue | dir tag applied |
| 1288 | build p1 | FALSE_COMPLETE x2 | RUNNING (fworker-wq1288) |
| 1293 | build p1 | PERMISSION_LOOP x2 | RUNNING, --repo /home/lumen/bin via needs |
| 1297 | fleet p1 | CITED_PATH kb/ (now base add_dir) | dispatchable |
| 1360 | pokemon p1 | restart(lumen-pokemon-live@lumen) refused 4x | allow row added (PR #352, 67571f5); needs tag set |
| 1416 | build p3 | CAPABILITY_DENIED services/ path | dispatchable |
| 1482 | build p2 | FALSE_COMPLETE (slice 5) | dispatchable |
| 339 390 855 1304 1008 358 | user / frank-hw | CITED_PATH pre-skip on a HUMAN lane (wrong class) | back in Frank's queue, frank_gated skip |
Other session (orch-ticket-reconcile) reopened 869 879 1122 1278 1387 1345 1338 in the same window; 869/879 sit at runaway_stuck (5 phantom attempts, see wq#1499), 1122 needs_unresolved (sentinel/probes tokens, wq#1466).

## Closed (superseded / landed, evidence checked)
1440 (slice E d327b5c in lumen-bin main), 1322 (PR#57 dffea81 in main), 1393 (foreman main==origin), 1333 (shipped as wq#1437/1438), 485 (BGB hosts gone).

## Stays DENIED / not worker-runnable — routed
Orchestrator (has Commander + secrets reach; do under lumen-change-safety):
- 927 / 940 / 1481 / 1490 — /home/lumen/bin primary on feat/wq1374 with 5c9dc58 unpushed + another lane's uncommitted wq edit. One job: stash, put primary on main, land 5c9dc58 via lumen-pr, make primary pull-only (1490). Reversible (git).
- 1388 (+1368) — land lumen-bin PR#61 a18ea88 (open, green).
- 1344 — close foreman PR#186.
- 1458 (p0) — governor persistent unit (needs sudo tee to /etc/systemd; v2 verb).
- 919 — bump pinned litellm image (compose under /home/lumen/agent, denied to workers); back up first.
- 1401 — split: scheduler code (worker-runnable, no verb) vs the reboot window (v2/Frank). Lint honestly refuses the combined text.
- 1403 — after 1467 (minerva/context credentials mirror) lands, /home/lumen/minerva can be swept + allowed.
- 1439 — rewrite detail without the literal /home/lumen/agent path (it is the example, not a read target); fworker.add_dirs_for still has no DIR_DENY.
Secrets (never a worker): 306, 1466, 1467, 1483, 1491 (sudo pw rotation, exposed twice).
Crown-jewel / irreversible (propose-don't-deploy, Frank present for Maat): 325 (vault HA drill), 463 (failback path Sobek-shaped), 1402 (fleet upgrade batch), 418 (12-node reboot window).
Hardware / physical (v2 verbs adb/fastboot/reboot/shutdown): 350, 364, 665, 679, 684, 709, 1016, 1044, 1432, 1152, 1157 (thoth :22 down since router reset 09-02).
Fires: 1421, 1435 fence-tripped (aq#794 dispatcher stall — now dispatchable=29, re-check; aq#1109 probe-synthetic-llm).

## Test-to-failure
i. dispatchable() before: 3 / 5 / 3 (passes 06:17-06:20Z) -> after: 29 (fdisp-20260908T062924Z, candidates 42). 
ii. 1293 spawned with --repo /home/lumen/bin from its needs tag; 1288 running. (broker-audited exec call: pending the first ssh/restart-tagged spawn — 1360/1204/1297 queue behind tight_mode min_pri 1.)
iii. never-list: lint derive on "restart(tailscaled@thor)" -> refused policy:never.nodes; 1466/1467/1483/1446/1474/1490/1491 -> capability_denied_by_policy pre-skip, zero spawns (unit test row + replay).
iv. 1122 -> needs_unresolved, 869/879 -> runaway_stuck: one skip line each per pass, no spawn, no LAUNCH_FAILURE.

## Defects found this session -> tickets
- wq#1498 `wq retire fworker-wq<id>` poisons the ticket's future spawns (3 dispatcher spawns refused live; 13 rows removed by hand).
- wq#1499 harness-fault attempts (1-2s, t1, exit 1) charged; 869/879 locked behind the runaway breaker.
