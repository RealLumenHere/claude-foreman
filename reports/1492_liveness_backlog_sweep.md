# wq#1492 — liveness-gate one-time backlog sweep (scope=all, 2026-09-08)

Design: research/LIVENESS_GATE.md §13. Code: harness/liveness_sweep.py (`--scope all`,
`backlog_tickets`), harness/liveness_clear.py (`sweep(scope=)`, `--scope`), harness/liveness.py
(four new deterministic probes: `ticket_done`, `pr_merged`, `file_contains`, `json_gte`),
harness/liveness_ledger.yaml (`bindings:` staged for 14 backlog tickets, 3 later dropped —
see §Bindings), ops/wq1492-liveness-backlog-sweep/ (run_sweep.py, render_report.py,
no_tag_reasons.yaml). Runs + backups: /home/lumen/foreman/_runs/wq1492/
(backups/queue.db.20260908T063054Z, liveness_ledger.yaml.*, liveness_state.json.*;
dryrun.json/.log, apply.json/.log, apply2.json/.log, table.md).

## Outcome

| run | tickets in scope | SATISFIED | STILL_BLOCKED | UNKNOWN | NO_TAG | closed |
|---|---|---|---|---|---|---|
| dryrun (06:30Z) | 107 | 4 | 8 | 0 | 95 | 0 (4 would_close) |
| apply (06:31Z) | 107 | 4 | 8 | 0 | 95 | **4** |
| apply2 (idempotence) | 103 | 0 | 8 | 0 | 95 | **0** |

Closed by the sweep (each = `wq note` + `wq liveness-close --verdict <verbatim>` + audit row):

- wq#703 [user/open] pstack FRANK GATE: enable Skill tool for foreman workers — `file_contains(/home/lumen/foreman/worker-settings-general.json, Skill) -> SATISFIED` (the allow-list already carries Skill, worker-settings-general.json:11).
- wq#765 [user/open] ops-audit FRANK: decisions this campaign will surface — `ticket_done(756), ticket_done(757), ticket_done(764) -> SATISFIED` (A6/A7/A14 all done; item 1 delivered per the detail).
- wq#843 [frank-funding/open] DECISION: raise cloud_ceiling 3→5 — `json_gte(/home/lumen/foreman/dispatcher/config.json, cloud_ceiling, 5) -> SATISFIED: cloud_ceiling=10` (decision moot).
- wq#1269 [fleet-fires/frank_gated] lumen-public-guardian unlock E1/E2 — `path_exists(/home/lumen/mailbot/.cf_deploy_token) -> SATISFIED` (its own 2026-09-07 note: E1+E2 done, only an OPTIONAL item left).

Kept STILL_BLOCKED with a binding now written into the ticket (self-clearing from here on,
picked up by the 30-min timer only if frank-gated, otherwise by a future `--scope all` run):
855 (fleet-update-check.timer inactive), 1008 (reddit.env missing), 1101 (both repos still
public), 1338 / 1358 (mailbot PR#3 / #5 open), 1344 (foreman PR#186 open), 1388 (lumen-bin
PR#61 open), 1473 (config.json still lacks frank-cred).

The backlog moved under the sweep: between the 05:5xZ snapshot (113 tickets) and the dry run
(107), orch-fleetops-broker closed 485, 983, 1322, 1333, 1393, 1440 by hand — three of them
(1322, 1333, 1440) on exactly the premise this sweep had staged (`pr_merged(lumen/lumen-bin, 57)`,
`ticket_done(1437,1438,1441)`, `pr_merged(lumen/lumen-bin, 63)`), which the dry run had already
probed SATISFIED before they vanished. Those bindings were dropped (a done ticket is out of
scope). Two tickets (1498, 1499) were filed mid-run; NO_TAG, listed below.

"Expect a large clear" did not happen, and that is the honest result: 95 of 107 tickets have
no deterministic premise. The categories (counted over apply.json ∩ no_tag_reasons.yaml) are
harness defects (HANG/PERMISSION_LOOP/CRASH/FALSE_COMPLETE/CAPABILITY_DENIED/evidence-gate: 25),
physical/purchase (24), plain open work (16), semantic traps (12), human judgment or
authorization (11) and prerequisite-deps (7) — every one with its reason in the table. The
largest *actionable* residue is the 16-row dep-met list (deps= all done, status still
blocked/open): those are unblock candidates, not closes. Orchestrator hand-close candidates
flagged in the trap rows: 1270 (a ticket filed by a pytest run — its guardian state path is
under /tmp/pytest-of-lumen), 1078 (decision recorded, no child ticket found), 1157 (thoth is
back online; verify qbit), 1356 (one leftover untracked .patch file), 927/940/1481 (all
superseded by 1490), 1121 (same defect as 1474).

## Binding rule (the decision that shaped the numbers)

A binding is written only when all-SATISFIED means the ticket's ASK is delivered. The ticket
text suggested two shortcuts that are semantic traps and were refused:

1. `ticket_done(N)` for "blocked on wq#N" (e.g. #306 citing wq#305). wq#305 is done, but
   #306's remaining work (Frank drops `.vw_master_pw`, run vw-migrate-secrets, shred) is not —
   a dep being done UNBLOCKS the ticket; it does not finish it. Closing would lose the work.
   `ticket_done` is bound only where the cited ticket delivers this ticket's outcome
   (superseded-by: #765 -> 756/757/764; #1333 -> 1437/1438/1441 before the orchestrator closed it).
   Prerequisite deps are reported in the dep-met list instead (no write path was added:
   SATISFIED -> liveness-close is the one verb, per the constraints).
2. `path_exists` for CITED_PATH_UNREACHABLE. Those tickets cite paths that EXIST on the
   host (`/home/lumen/bin/lumen-pr` for #1121, `/home/lumen/kb/...` for #1297/#1304); the
   worker just could not reach them. `path_exists` would close live bugs on the spot.
   `path_exists` is bound only where the deliverable IS the file (#1008 reddit.env,
   #1269 .cf_deploy_token).

`node_healthy` was not bound for hardware tickets whose node has never joined the tailnet
(hapi-1, sentinel): the probe answers UNKNOWN for a hostname absent from `tailscale status`
and would flag once and then sit forever. thoth IS back online (Online=true 06:2xZ) but
#1157's ask is qbit.lumennode.uk up + a LAN sshd, so it is a trap, listed for the orchestrator.

## Test-to-failure (actual run output)

(i) known-done ticket auto-closes with a recorded probe verdict — live apply run, apply.log:

```
  wq#765   SATISFIED     closed       [user/open] CLOSED wq#765
           SATISFIED     ticket_done(756) -> wq#756 done
           SATISFIED     ticket_done(757) -> wq#757 done
           SATISFIED     ticket_done(764) -> wq#764 done
  wq#1269  SATISFIED     closed       [fleet-fires/frank_gated] CLOSED wq#1269
           SATISFIED     path_exists(/home/lumen/mailbot/.cf_deploy_token) -> /home/lumen/mailbot/.cf_deploy_token exists
```
Row confirmation (`wq show 1269 --json`): status=done, notes end with
`[liveness auto-clear wq#1478] from frank_gated via Liveness: line [NO-ARTIFACT: liveness: path_exists(/home/lumen/mailbot/.cf_deploy_token) -> SATISFIED: ... exists]`.
Unit form: harness/tests/test_liveness_clear.py::test_scope_all_reaches_non_frank_tickets_and_is_idempotent
(build-lane blocked ticket with `Liveness: ticket_done(<done seed>)`, invisible to the default
scope, closes under scope=all, second apply = 0 new closes).

(ii) genuinely blocked stays — live apply.log and apply2.log, identical:

```
  wq#1101  STILL_BLOCKED kept         [frank-cred/open]
           STILL_BLOCKED repo_is(private, frankstupak/lumen-content) -> frankstupak/lumen-content is public
           STILL_BLOCKED repo_is(private, frankstupak/lumen-blog) -> frankstupak/lumen-blog is public
```
`wq show 1101 --json`: status=open, last note is the 06:03Z binding write, nothing from this run.

(iii) UNKNOWN stays and flags once — no probe returned UNKNOWN on the live backlog (0 flags),
so the transport-error case is forced in
harness/tests/test_liveness_clear.py::test_scope_all_transport_error_is_unknown_flagged_once:
a test ledger binds `cred_alive(<tmp token>, http://127.0.0.1:9/verify)` (bogus verify_url,
real connection refused, no monkeypatch) -> `verify http 0`, verdict UNKNOWN, action
`flagged`; second sweep `flag_deduped`; row status still blocked; exactly one
`[liveness] UNKNOWN` note; no `closed` audit row.

```
$ python3 -m pytest harness/tests/test_liveness_clear.py -q -k "scope_all or ii_genuinely or iv_" -v
7 passed, 8 deselected in 6.59s
$ python3 -m pytest harness/tests/test_liveness.py harness/tests/test_liveness_clear.py -q
33 passed in 24.10s        (25 before this change + 8 new)
```

(iv) nothing closes without a probe verdict — the live audit file after the run:

```
$ python3 - (reads /home/lumen/foreman/harness/runtime/liveness_audit.jsonl)
audit rows 31 closed 10 closed-without-SATISFIED-probes []
```
All 10 `action=closed` rows (6 from the 06:03Z timer pass, 4 from this sweep) carry a
non-empty `probes[]` whose every verdict is SATISFIED; the four from this run are listed
verbatim under "closes in liveness_audit.jsonl" below.

Firewall: test_iv_exec_allow_deny_byte_unchanged + test_iv_firewall_import_surface still
green (pins untouched: capability.preflight / policy_grants / effective_needs /
effective_add_dirs, fworker.resolve_exec_grant / assert_spawn_capability). The timer's default
scope is unchanged (test_scope_default_unchanged_for_timer; the unit still runs
`liveness_clear.py --apply --write-bindings` with no `--scope`).

## Ledger table (from apply.json; category derived from the probe name)

| category | tickets | SATISFIED | STILL_BLOCKED | UNKNOWN | NO_TAG | closed |
|---|---|---|---|---|---|---|
| repo | 5 | 0 | 5 | 0 | 0 | 0 |
| path | 5 | 3 | 2 | 0 | 0 | 3 |
| service | 1 | 0 | 1 | 0 | 0 | 0 |
| ticket-dep | 1 | 1 | 0 | 0 | 0 | 1 |
| unprobeable | 95 | 0 | 0 | 0 | 95 | 0 |
| **total** | 107 | | | | | 4 |

### repo (5)

| id | lane | prior status | probe | verdict | action | reason / detail |
|---|---|---|---|---|---|---|
| 1101 | frank-cred | open | repo_is(private, frankstupak/lumen-content)<br>repo_is(private, frankstupak/lumen-blog) | STILL_BLOCKED | kept | STILL_BLOCKED: frankstupak/lumen-content is public<br>STILL_BLOCKED: frankstupak/lumen-blog is public |
| 1338 | mailbot | open | pr_merged(lumen/mailbot, 3) | STILL_BLOCKED | kept | STILL_BLOCKED: lumen/mailbot#3 open |
| 1344 | ops | blocked | pr_merged(lumen/claude-foreman, 186, closed) | STILL_BLOCKED | kept | STILL_BLOCKED: lumen/claude-foreman#186 open |
| 1358 | mailbot | blocked | pr_merged(lumen/mailbot, 5) | STILL_BLOCKED | kept | STILL_BLOCKED: lumen/mailbot#5 open |
| 1388 | build | blocked | pr_merged(lumen/lumen-bin, 61) | STILL_BLOCKED | kept | STILL_BLOCKED: lumen/lumen-bin#61 open |

### path (5)

| id | lane | prior status | probe | verdict | action | reason / detail |
|---|---|---|---|---|---|---|
| 703 | user | open | file_contains(/home/lumen/foreman/worker-settings-general.json, Skill) | SATISFIED | closed | SATISFIED: worker-settings-general.json contains 'Skill' |
| 843 | frank-funding | open | json_gte(/home/lumen/foreman/dispatcher/config.json, cloud_ceiling, 5) | SATISFIED | closed | SATISFIED: cloud_ceiling=10 vs min 5 |
| 1008 | user | open | path_exists(/home/lumen/secrets/reddit.env) | STILL_BLOCKED | kept | STILL_BLOCKED: /home/lumen/secrets/reddit.env missing |
| 1269 | fleet-fires | frank_gated | path_exists(/home/lumen/mailbot/.cf_deploy_token) | SATISFIED | closed | SATISFIED: /home/lumen/mailbot/.cf_deploy_token exists |
| 1473 | auto | open | file_contains(/home/lumen/foreman/dispatcher/config.json, frank-cred) | STILL_BLOCKED | kept | STILL_BLOCKED: config.json lacks 'frank-cred' |

### service (1)

| id | lane | prior status | probe | verdict | action | reason / detail |
|---|---|---|---|---|---|---|
| 855 | user | open | service_active(fleet-update-check.timer@lumen) | STILL_BLOCKED | kept | STILL_BLOCKED: fleet-update-check.timer@lumen inactive |

### ticket-dep (1)

| id | lane | prior status | probe | verdict | action | reason / detail |
|---|---|---|---|---|---|---|
| 765 | user | open | ticket_done(756)<br>ticket_done(757)<br>ticket_done(764) | SATISFIED | closed | SATISFIED: wq#756 done<br>SATISFIED: wq#757 done<br>SATISFIED: wq#764 done |

### unprobeable (95)

| id | lane | prior status | probe | verdict | action | reason / detail |
|---|---|---|---|---|---|---|
| 11 | user | open | — | NO_TAG | none | [physical] Nest UART bench recon — needs the board on the bench |
| 12 | user | open | — | NO_TAG | none | [physical] Frank's phone: reload PWA + confirm messaging cutover |
| 120 | auto | blocked | — | NO_TAG | none | [prereq] hapi-1 has never joined the tailnet (node_healthy would be UNKNOWN forever); boot is the prerequisite for the PoC, not the PoC |
| 124 | auto | blocked | — | NO_TAG | none | [physical] NVMe purchase + install before any pilot |
| 160 | frank-funding | open | — | NO_TAG | none | [physical] purchase (SPI programmer/clip/spare chip) |
| 266 | build | blocked | — | NO_TAG | none | [prereq] hapi-1 boot is the prerequisite; the eval run is the work |
| 306 | build | blocked | — | NO_TAG | none | [trap] deps=305 is done but the ask (VW migration) needs Frank to drop .vw_master_pw then a run; path_exists on the pw file would mean 'dropped', not 'migrated', and the file is shredded afterwards |
| 325 | build | blocked | — | NO_TAG | none | [physical] live HA drill window with Frank + destructive Wadjet release |
| 339 | user | open | — | NO_TAG | none | [physical] Frank's phone ntfy resubscription; publisher patch apply is the fleet half |
| 349 | frank-re | blocked | — | NO_TAG | none | [physical] Miko 3 USB-UART wiring on Thor bench |
| 350 | user | blocked | — | NO_TAG | none | [physical] 24-port switch bench walk |
| 358 | frank-hw | open | — | NO_TAG | none | [physical] apply pack on Sia after Bes soak; no soak probe in vocabulary |
| 364 | frank-re | blocked | — | NO_TAG | none | [physical] SPI dump needs clip on the blade |
| 390 | user | open | — | NO_TAG | none | [human] policy decision (auto-reboot watchdog); ARMED flag would be the effect, not the decision |
| 391 | frank-funding | open | — | NO_TAG | none | [physical] purchase (IP-KVM) |
| 402 | frank-hw | blocked | — | NO_TAG | none | [physical] watch8 wireless debugging must be re-enabled on-device |
| 418 | frank-hw | open | — | NO_TAG | none | [physical] reboot window needs Frank present for Lumen/dbx; no pending-reboot probe |
| 463 | ops | blocked | — | NO_TAG | none | [harness] HANG; underlying work is a Frank-gated fence drill anyway |
| 484 | pokemon | blocked | — | NO_TAG | none | [harness] PERMISSION_LOOP; deps=511 done is a prerequisite |
| 501 | user-projects | open | — | NO_TAG | none | [human] 4-item repo disposition; DELETE is indistinguishable from private via repo_is; orch decided rom-transfer-tmp DELETE 09-07, execution needs Frank's admin token |
| 558 | user | open | — | NO_TAG | none | [human] release/tag removal on claude-command-center; no release probe; orch decided REMOVE 09-07 and said it was dispatching — no child ticket found by title |
| 600 | frank-hw | blocked | — | NO_TAG | none | [harness] HANG x4; proposal is Frank-gated design |
| 619 | build | open | — | NO_TAG | none | [harness] FALSE_COMPLETE x5; calibration run is the work |
| 665 | frank-hw | blocked | — | NO_TAG | none | [physical] watch must be online at the bench |
| 679 | questfree | blocked | — | NO_TAG | none | [physical] headset USB-connected + adb allowlisted |
| 683 | questfree | blocked | — | NO_TAG | none | [harness] PERMISSION_LOOP x3 |
| 684 | questfree | blocked | — | NO_TAG | none | [harness] wrong-repo dispatch (fixed by lane edit); next dispatch runs it |
| 709 | fleet-safety | open | — | NO_TAG | none | [work] open build work (lumen-guardian), no premise |
| 869 | build | open | — | NO_TAG | none | [harness] CRASH x5 |
| 879 | build | open | — | NO_TAG | none | [harness] CRASH x5 |
| 891 | research | blocked | — | NO_TAG | none | [trap] block reason (cerebras 402) cleared per 09-07 note, but the ask is the re-run; balance_over SATISFIED != re-gate run — orchestrator unblock |
| 894 | foreman-tok | blocked | — | NO_TAG | none | [human] authorization to burn Max-20x limit budget |
| 895 | foreman-tok | blocked | — | NO_TAG | none | [human] authorization to burn Max-20x limit budget |
| 913 | build | open | — | NO_TAG | none | [harness] HANG x4 |
| 919 | build | blocked | — | NO_TAG | none | [harness] HANG x4; litellm bug |
| 927 | build | blocked | — | NO_TAG | none | [trap] bin primary-tree drift; superseded by wq#1490 (canonical tree pull-only) — orchestrator dedupe, no git-state probe in vocabulary |
| 940 | ops | blocked | — | NO_TAG | none | [trap] same class as 927/1481/1490; ref move on the primary tree |
| 941 | frank-hw | blocked | — | NO_TAG | none | [physical] M70s provisioning at the machine; 'sentinel' never joined the tailnet |
| 1016 | user | blocked | — | NO_TAG | none | [physical] Thor fan swap (part ordered 08-28) |
| 1044 | research | blocked | — | NO_TAG | none | [physical] Frank+Claude bench, explicitly do-not-dispatch |
| 1053 | auto | blocked | — | NO_TAG | none | [work] Node sd_notify primitive needs its own design; deps=1043 done is a prerequisite |
| 1071 | user | frank_gated | — | NO_TAG | none | [physical] Nest DFU reflash on the bench |
| 1077 | research | frank_gated | — | NO_TAG | none | [human] ralph-loop adoption decision |
| 1078 | research | open | — | NO_TAG | none | [trap] orch decided 09-07 (port make-bot-ui, skip the rest) and said it was dispatching; no child ticket found by title — orchestrator close by hand |
| 1105 | simpill | open | — | NO_TAG | none | [work] upstream PR not yet opened; pr_merged needs a number |
| 1121 | build | open | — | NO_TAG | none | [trap] CITED_PATH_UNREACHABLE cites /home/lumen/bin/lumen-pr which EXISTS — path_exists would close a live bug; same defect re-filed as wq#1474 (orchestrator dedupe) |
| 1122 | build | open | — | NO_TAG | none | [harness] CRASH x4 |
| 1146 | build | open | — | NO_TAG | none | [work] open build work (auto-decomposer) |
| 1152 | frank-re | blocked | — | NO_TAG | none | [harness] needs --add-dir /home/lumen/projects/nest respawn |
| 1155 | frank-re | blocked | — | NO_TAG | none | [prereq] deps wq#1325 (LANE_REPO fix) is done -> unblock; the D4/D6/D7 landing is the work |
| 1156 | auto | open | — | NO_TAG | none | [work] disk growth hunt; no disk-usage probe in vocabulary |
| 1157 | build | blocked | — | NO_TAG | none | [trap] thoth is back on the tailnet (Online=true 09-08) but the ask is qbit.lumennode.uk up + LAN sshd; node_healthy(thoth) SATISFIED != qbit up — orchestrator verify + close by hand |
| 1197 | mailbot | blocked | — | NO_TAG | none | [harness] HANG x5 |
| 1204 | build | open | — | NO_TAG | none | [prereq] verify-pass of wq#1168 (done); the verification is the work |
| 1207 | build | open | — | NO_TAG | none | [harness] FALSE_COMPLETE x5; verify-pass of wq#1170 (done) |
| 1270 | fleet-fires | blocked | — | NO_TAG | none | [trap] TEST ARTIFACT: guardian state path is /tmp/pytest-of-lumen/... — filed by a pytest run, not a fire; waiting on approvals/wq1270.approved that nobody should write. Orchestrator: close as bogus |
| 1278 | build | open | — | NO_TAG | none | [harness] PERMISSION_LOOP x3 |
| 1295 | build | blocked | — | NO_TAG | none | [harness] PERMISSION_LOOP x2 |
| 1297 | fleet | open | — | NO_TAG | none | [prereq] CITED_PATH_UNREACHABLE (kb/); the guard is the work |
| 1304 | user | open | — | NO_TAG | none | [physical] bench RE toolchain install at the bench |
| 1341 | research | frank_gated | — | NO_TAG | none | [physical] Seshat ata1 power/mSATA swap |
| 1345 | pokemon | open | — | NO_TAG | none | [prereq] deps 1375+1376 done -> unblock; the both-directions verification is the work |
| 1350 | ops | blocked | — | NO_TAG | none | [harness] CRASH x2; deps=1347 done |
| 1356 | mailbot | frank_gated | — | NO_TAG | none | [trap] premise = mailbot primary tree clean; no git_clean probe (deliberately not added: it would only ever report STILL_BLOCKED on the leftover .patch file); notes say resolved except one untracked file — orchestrator rm + close |
| 1360 | pokemon | open | — | NO_TAG | none | [trap] CAPABILITY_DENIED restart; streamer.ts present on main != kiosk chat panel wired |
| 1368 | build | blocked | — | NO_TAG | none | [harness] CAPABILITY_DENIED x6; dispatcher half unverified |
| 1386 | auto | open | — | NO_TAG | none | [work] open auto work |
| 1387 | build | open | — | NO_TAG | none | [harness] CAPABILITY_DENIED curl x7; verify of wq#1375 (done) |
| 1398 | build | blocked | — | NO_TAG | none | [harness] /home/lumen/langfuse outside sandbox |
| 1401 | build | blocked | — | NO_TAG | none | [prereq] parked on wq#1415 (launcher fix, done) -> unblock; scheduler is the work |
| 1402 | build | blocked | — | NO_TAG | none | [work] multi-service maintenance batch |
| 1403 | build | blocked | — | NO_TAG | none | [harness] CITED_PATH_UNREACHABLE (backups/wq-proposals) |
| 1416 | build | open | — | NO_TAG | none | [harness] CAPABILITY_DENIED x3 |
| 1421 | fleet-fires | blocked | — | NO_TAG | none | [human] fire fence tripped x2; needs a human |
| 1429 | frank-re | open | — | NO_TAG | none | [physical] Miko3 patch; no live flash without Frank |
| 1431 | auto | open | — | NO_TAG | none | [work] recurring log review |
| 1432 | frank-re | open | — | NO_TAG | none | [physical] Miko3 mtkclient write on device |
| 1435 | fleet-fires | blocked | — | NO_TAG | none | [human] fire fence tripped x2; needs a human |
| 1439 | build | blocked | — | NO_TAG | none | [harness] FALSE_COMPLETE; deps=1438 done is a prerequisite |
| 1446 | build | open | — | NO_TAG | none | [work] dispatcher deliverable-repo fix |
| 1452 | frank-re | open | — | NO_TAG | none | [physical] Miko Mini teardown |
| 1458 | build | blocked | — | NO_TAG | none | [trap] governor runs only as a transient lumen-job unit today; persistent unit name unknown until built (service_active needs a name) |
| 1466 | build | open | — | NO_TAG | none | [work] secrets move; a negative file_contains would print nothing useful and misses the .bak copies |
| 1467 | build | open | — | NO_TAG | none | [work] secrets mirror fix |
| 1468 | build | blocked | — | NO_TAG | none | [harness] evidence gate PIN x3 |
| 1474 | auto | open | — | NO_TAG | none | [work] lumen-pr guard carve-out (same defect as 1121) |
| 1480 | user | frank_gated | — | NO_TAG | none | [human] PAT rotation on Frank's account; cred_alive cannot tell old from new (a sha-prefix probe was considered and rejected: one ticket, STILL_BLOCKED either way) |
| 1481 | fleet | open | — | NO_TAG | none | [trap] bin primary on a feature branch; superseded by wq#1490 |
| 1482 | build | open | — | NO_TAG | none | [harness] FALSE_COMPLETE |
| 1483 | build | open | — | NO_TAG | none | [work] secrets scrub + token rotation |
| 1488 | user | frank_gated | — | NO_TAG | none | [human] LiteLLM key budget spend decision; no budget probe (litellm /key/info) in vocabulary |
| 1490 | ops | open | — | NO_TAG | none | [work] lumen-bin hygiene refactor |
| 1491 | ops | open | — | NO_TAG | none | [human] fleet sudo password rotation |
| 1498 | build | open | — | NO_TAG | none | [work] filed during the run (wq retire / fworker identity); open build work |
| 1499 | build | open | — | NO_TAG | none | [work] filed during the run (harness-fault attempts charged); open build work |

### dep-met unblock candidates (deps= all done; NOT closed — the work remains)

| id | status | deps (all done) |
|---|---|---|
| 306 | blocked | wq#305 |
| 402 | blocked | wq#401 |
| 484 | blocked | wq#511 |
| 619 | open | wq#614, wq#617 |
| 683 | blocked | wq#681, wq#682 |
| 684 | blocked | wq#681, wq#682 |
| 891 | blocked | wq#849 |
| 1053 | blocked | wq#1043 |
| 1077 | frank_gated | wq#1059 |
| 1078 | open | wq#1059 |
| 1207 | open | wq#1170 |
| 1278 | open | wq#511 |
| 1345 | open | wq#1375, wq#1376 |
| 1350 | blocked | wq#1347 |
| 1387 | open | wq#1375 |
| 1439 | blocked | wq#1438 |

### closes in liveness_audit.jsonl (this run's actor, action=closed)

- wq#703 [user/open -> done] 2026-09-08T06:31:42Z — file_contains(/home/lumen/foreman/worker-settings-general.json, Skill) -> SATISFIED: worker-settings-general.json contains 'Skill'
- wq#765 [user/open -> done] 2026-09-08T06:31:43Z — ticket_done(756) -> SATISFIED: wq#756 done; ticket_done(757) -> SATISFIED: wq#757 done; ticket_done(764) -> SATISFIED: wq#764 done
- wq#843 [frank-funding/open -> done] 2026-09-08T06:31:44Z — json_gte(/home/lumen/foreman/dispatcher/config.json, cloud_ceiling, 5) -> SATISFIED: cloud_ceiling=10 vs min 5
- wq#1269 [fleet-fires/frank_gated -> done] 2026-09-08T06:31:46Z — path_exists(/home/lumen/mailbot/.cf_deploy_token) -> SATISFIED: /home/lumen/mailbot/.cf_deploy_token exists

### UNKNOWN flags for the orchestrator (0)

- none
