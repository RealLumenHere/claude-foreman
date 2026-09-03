# wq#1302 — n8n as a replacement for the fleet's cron glue

**Verdict: SKIP.** Not "not yet" — the fleet's cron glue is the wrong shape for n8n, and
adopting it would trade 109 independently-supervised systemd timers for one always-on
Node service holding root-equivalent reach across the tailnet. Measured on this node:
**0 of 109 fleet timers can be expressed as an n8n workflow without granting n8n a new
capability** (shell on a specific node, root, or stored SSH credentials).

Evidence is mechanical and reproducible: `cron_migration_audit.py` (this dir), with the
masked live snapshot in `inventory.json` and tests in `tests/`.

## 1. What was measured

```
python3 cron_migration_audit.py --deep --json inventory.json
```

The audit enumerates every systemd timer, pairs it with the service it activates, and
scores that job against the capabilities n8n would have to be handed to run it. A job with
zero blockers is one an n8n **Schedule Trigger + HTTP Request** node replaces outright —
no shell, no node-locality, no credential custody. That is n8n's actual sweet spot, so it
is the fair test.

```
timers: 131 total, 22 distro-managed, 109 fleet glue
movable as pure HTTP workflow: 0/109
```

| blocker | jobs | what n8n would need |
|---|---|---|
| local-exec | 109 | an Execute Command node on that node, or an SSH node + stored key |
| local-state | 107 | filesystem access to `/home/lumen`, the wq SQLite db, git worktrees |
| secret-handling | 69 | custody of PATs/keys the job handles today via file perms |
| self-watch | 60 | n8n would be watching itself (see §4) |
| bridge-sensitive | 44 | the bridge-exclusion policy moves out of git into a DB (see §3) |
| systemd-control | 24 | permission to start/stop/inspect units |
| container-control | 22 | the docker socket |
| root-privilege | 8 | root |

The shallow pass (ExecStart only) reports `systemd-control 0, container-control 0,
bridge-sensitive 8, secret-handling 9`; reading the wrapper scripts those ExecStarts
invoke raises them to 24/22/44/69. 64 of 109 ExecStarts are bare `python3 <script>` — the
coupling lives inside the scripts, which is exactly what a star-count-driven "n8n could
replace this" read misses. The deep pass reads only first-level scripts and does not follow
what those scripts invoke, so its numbers are a floor, not a ceiling.

**The closest-to-movable jobs**, two blockers each, 10 of 109: `dc-patch-tripwire`,
`deepseek-cost-tracker`, `lumen-backup-prune`, `lumen-backup-verify`, `lumen-bc250-watch`,
`lumen-bench-checker`, `lumen-scratch-sweep`, `renovate-gitea`, `system-map` (all
`local-exec` + `local-state`) and `wq394-netdata-sampler` (`local-exec` + `root-privilege`).
Every one still runs a local command against node-local state, so in n8n each becomes an
Execute Command node wrapping the same script — the workflow engine buys a web UI over the
existing shell command and nothing else. Migrating them is pure churn: identical behaviour,
one new dependency in the path.

## 2. Self-hosted footprint

Community n8n is a Node service with a browser editor: SQLite by default, Postgres for
anything sustained, and **queue mode — the only way to run concurrent executions safely —
adds Redis plus separate worker processes** (`VERIFY` against current docs; network
verification was unavailable in this session, see §6). Execution history lands in that
database and grows without pruning config.

That is the same ClickHouse-tax argument the Fable review used to demote Langfuse
(`kb/tooling-findings-review.md` §1), applied to a component that would sit on the critical
path rather than beside it. Today, a job's failure domain is one `systemd` unit. After
migration, the failure domain of every migrated job is one Node process, its DB, and its
Redis — and n8n stopping is *silent*, the exact failure class this fleet spent wq#1184 and
the Tier-0 crons eliminating.

Systemd already provides what the engine would be re-implementing: `Persistent=` catch-up
after downtime, `RandomizedDelaySec=`, `OnFailure=` alerting, `Restart=`, per-unit `User=`
and sandboxing, `journalctl` history, and — via `ops/wq1184-gatus-health-checks/` —
stamp-file dead-man coverage per job.

## 3. Bridge safety

Two hard problems, both fatal on their own:

1. **Workflows live in a database, not in git.** Git source control for workflows is an
   n8n enterprise feature (`VERIFY`); the community path is `n8n export:workflow` on a cron
   — i.e. you write cron glue to back up the thing that replaced your cron glue. The
   fleet's bridge policy is currently a reviewable file with the forbidden router/bridge
   IPs named in a header comment (`kb/gatus/config.yaml`, per `tooling-findings-review.md`
   §4.3). Moving 44 bridge-sensitive jobs into a UI-edited DB deletes that review surface.
   This is precisely the reason Gatus was chosen over Uptime Kuma; adopting n8n reverses
   that decision by the back door.
2. **A visual builder invites exactly the wrong nodes.** n8n ships ping/HTTP/SSH/TCP
   primitives and a schedule trigger, so the cheapest way to answer "is the bridge up" in
   its UI is a ping loop against the router — the `speedtest-tracker` hazard
   (`tooling-findings-review.md` §4.1) with a friendlier interface. No probe was run against
   any router or bridge in this evaluation, and none is needed to reach that conclusion.

Add the credential-custody problem: n8n encrypts stored credentials with a single
`N8N_ENCRYPTION_KEY` on the instance. Consolidating 69 secret-handling jobs' credentials
plus SSH keys for the other nodes behind one web UI's key is a blast-radius increase, and
the web UI is the sort of surface Tier-0 instinct 5 exists for.

## 4. Who watches the watcher

60 of 109 fleet jobs are themselves watchdogs, dead-man switches, drift detectors or
self-heal passes. Moving them inside n8n means n8n's own liveness becomes the precondition
for detecting that anything else broke — a single silent absence that mutes 60 alarms at
once. That is the 2026-09-01 incident shape (`kb/tooling-findings.md`: five days brainless,
evidence unread) generalised to the whole fleet, and it is why the Opus plan review already
rejected Hatchet/Temporal on category grounds (`kb/fleet-upgrade-plan-review.md` §2): you
would be adding an unmonitored critical-path datastore to a fleet whose thesis is "we don't
watch what we already run."

## 5. What would flip this verdict

Write the triggers down so the question does not get re-litigated on star count:

- **Multi-step, long-running, external-API orchestration appears** — a flow that calls 3+
  third-party SaaS APIs with retries, waits and human approval steps, where the value is the
  integration catalogue and not the scheduling. That is n8n's real product.
- **Non-engineer authorship becomes a requirement.** The visual editor's payoff is someone
  editing automation without writing Python. On a solo fleet whose operator writes the
  scripts, it is pure overhead.
- **Fleet glue stops being node-local** — if jobs move behind HTTP APIs (a foreman REST
  surface, a wq HTTP API), the `local-exec` blocker disappears for real and the audit's
  movable count rises above zero. Re-run `cron_migration_audit.py --deep` to check; that is
  what the tool is for.

Until then the cheaper answer to "the cron glue is hand-written and sprawling" is the
inventory itself: 131 timers with no single place to see them. `inventory.json` is that
place, and it costs no new service.

## 6. Confidence and gaps

- **High confidence, locally verified:** every number in §1 — it comes from `systemctl` on
  this node via the committed tool, and `tests/test_cron_migration_audit.py` pins the
  headline claim against `inventory.json`.
- **Training knowledge, `VERIFY` before acting on:** n8n's Sustainable Use License terms
  (fair-code, not OSI; free for internal use, no reselling/hosting-for-others — fine for a
  solo fleet, so licensing is *not* the reason to skip), git source control being
  enterprise-gated, queue mode's Redis requirement, and the ticket's 203k star figure.
  `WebFetch` and `WebSearch` were both unapproved in this session, as they were in the two
  kb reviews this ticket cites, so no n8n claim here rests on live docs.
- **Not measured:** other fleet nodes, and classic crontabs. This audit ran on the foreman
  node and covers systemd timers only — `crontab -l` is not on the build-worker allowlist
  (ISSUE-LOG row 137), so any `cron.d`/user-crontab jobs are outside these counts. The tool
  is node-local by design, so a full-fleet figure means running it per node. Neither gap is
  likely to move the conclusion: the blockers are structural (local shell, local state,
  node-local supervision), not artifacts of this node or of systemd as the scheduler.

## Files

| File | Purpose |
|---|---|
| `cron_migration_audit.py` | timer inventory + migration-blocker classifier, masked output |
| `inventory.json` | masked live snapshot, 2026-09-03, foreman node |
| `tests/test_cron_migration_audit.py` | offline tests incl. a fixture job that IS movable |

Run: `python3 cron_migration_audit.py --deep`, `python3 tests/test_cron_migration_audit.py`.
