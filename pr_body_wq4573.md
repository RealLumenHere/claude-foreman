# wq#4573 judgment: disposition of wq#4540 after wq#4567's CONTRADICT

## What

wq#4567 (verify-pass) found wq#4540 closed `done` with PR #2403 unmerged and its own
acceptance probe — `claude_context.sh --core | wc -c` ≤ 45 KB — never run, because the diff
never touches the minerva file that produces that number and `install.sh` step 4 only *prints*
the wiring seds. It asked: reopen wq#4540, or leave done and file a scoped follow-up?

**Ruling: leave wq#4540 `done`; carry the measurement on a scoped successor.**

## Why

- wq#4540's closure was **on the record**, not a silent false-complete: its notes carry
  `[UNMERGED-PR]` and state the rest is staged because minerva is unreachable.
  `research/TICKET_CLOSURE_AUDIT.md:71-72` draws exactly that line between the 13 untagged
  F1 false-completes and the 6 deliberate tagged closes.
- The build half is real and was **independently reproduced** by wq#4567 (9 + 14 tests from a
  clean extraction of `e2a32d90`). Marking it rework punishes a worker that delivered inside
  its grants.
- Reopening into `lane=build` **redispatches into the same wall** (ISSUE-LOG 1527;
  `harness/fleet-exec-policy.yaml:67` excludes `/home/lumen/minerva` by name, wq#1467). Once
  re-laned and re-scoped it is a new ticket wearing an old id.

The actual defect is a **missing successor**: the staging doctrine (wq#4537/#4539/#4540) ships
an `install.sh` and nothing files the deploy. wq#4537 got deployed only because an orchestrator
session with minerva write happened to be driving that lineage; wq#4540 was claimed four
minutes after it closed and had no such session behind it.

## How

Read the evidence rather than the summary: re-confirmed PR #2403 unmerged
(`git merge-base --is-ancestor e2a32d90 origin/main` → false), read `install.sh` step 4 out of
the PR branch to confirm the wiring is `cat`-printed and not applied, confirmed
`/home/lumen/bin/claude_context.sh` is a 2-line `exec` wrapper (`grep -c audience` → 0), and
checked the queue for a pre-existing successor (none: only wq#4567 and wq#4573 referenced
4540). Then located the grant surface — `harness/capability_lint.py:46-54` `DIR_DENY_PREFIXES`,
`harness/fleet-exec-policy.yaml:61-73` `read_dirs`, `harness/agents.yaml:98-116` archetype
`add_dirs` — to establish that a dispatchable deploy path already exists without minting a grant.

## Actions

| | |
|---|---|
| **wq#4576** [ops, deps=4540, archetype `rca-investigation`] | Carries the unrun measurement: land PR #2403 → install steps 2-3 → apply the wiring → measure vs 45,000 B → close in wq#4537's `[DEPLOYED:]`/`[DEPLOY-VERIFY:]` shape. Rides the **standing** `/home/lumen/minerva` grant on that archetype (`harness/agents.yaml:98-116`, wq#2577) — no new grant minted, wq#1467 stands, and the ISSUE-LOG 1528 guard gap is explicitly off limits. |
| **wq#4577** [build, pri 3] | Class fix: refuse `done` when the notes admit a staged-for-another-session deliverable and no successor deps-links the closer. |
| `wq note 4540` | The closed ticket now says on its face that its probe never ran, and names wq#4576. |
| ISSUE-LOG 1537 → `resolved`, new row **1542** | 1542 records two latent defects found while scoping the deploy: `install.sh:26` would `install` over the git-tracked lumen-bin `fleet-tools-render` (wq#886/#1538 drift class — use the ISSUE-LOG 239 PR + `ensure_primary_file_deploy_sync.py` route instead), and step 4's `FLEET_TOOLS_BOOT.md` sed depends on step 1 having produced that file. |

## Testing

No code changes, so no suite applies to this branch. The verification that mattered is the
evidence re-check in **How** above; each claim in the report cites the command or `path:line`
it came from. The successor's own probe is built to be falsifiable (wq#2630): wq#4576's
`Liveness:` line is `grep -q -- 'instincts_render.py --audience boot'
/home/lumen/minerva/context/claude_context.sh`, which is **false today** and goes true exactly
when the wiring lands.

## Risk / rollback

Low — the only repo change is one added report. The judgment's risk is queue state, not code:
if wq#4576 goes unclaimed, a `done` ticket plus an unworked successor is the same orphan this
ruling set out to fix. Rollback is a status flip plus a note: reopen wq#4540 in `lane=ops` and
fold wq#4576 into it (report §6). Nothing was deployed, no grant was widened, and PR #2403 was
left exactly as it was.

## Reviewer notes

Judgment ticket — the deliverable is the decision, its reasoning, and the queue state that
follows from it. One file added: `reports/wq4573_judgment_wq4540_boot_budget_disposition.md`.
§6 of the report states what would reverse the ruling: if wq#4576 goes unclaimed, a `done`
ticket plus an unworked successor is the same orphan, and reopening wq#4540 in `lane=ops`
becomes the cheaper error.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
