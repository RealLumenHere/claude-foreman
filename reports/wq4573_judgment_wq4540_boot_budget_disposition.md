# wq#4573 judgment: disposition of wq#4540 after wq#4567's CONTRADICT

**Question (wq#4567, archetype 8):** wq#4540 (BOOT BUDGET — `claude_context.sh --core` is
89,865 B) closed `status=done` with PR claude-foreman#2403 still open, and its own acceptance
probe — `export WQ_AGENT=x; bash claude_context.sh --core | wc -c` ≤ 45 KB — never run against
`/home/lumen/minerva/context/claude_context.sh`, a file the diff never touches. Reopen wq#4540,
or leave it done and open a scoped follow-up with the fs grants to apply `install.sh` step 4 and
re-measure?

**Ruling: leave wq#4540 `done`; carry the measurement on a scoped successor.**
Successor filed as **wq#4576** [ops]. Class fix filed as **wq#4577** [build]. A correcting note
is appended to wq#4540 itself so the gap is visible from the closed ticket, not only from a
verify report.

## 1. wq#4567's finding is correct and independently re-confirmed here

- PR #2403 is still unmerged at judgment time: `git merge-base --is-ancestor e2a32d90
  origin/main` → false; `origin/feat/wq4540-boot-budget-claude-context-sh-core-is-89-a1` exists,
  nothing of wq#4540 is on `main`.
- The wiring really is print-only. `install.sh:38-59` (read out of the PR branch) is
  `echo "== 4/4: claude_context.sh — NOT applied, apply by hand"` followed by a `cat <<'EOF'`
  of the two `sed` one-liners.
- `/home/lumen/bin/claude_context.sh` is a 2-line `exec` wrapper into the minerva file
  (`grep -c audience` → 0), so no readable copy of the measured file was touched by the diff.
- No successor existed before this judgment: nothing in the queue referenced 4540 except
  wq#4567 (verify) and wq#4573 (this ticket).

## 2. Why not reopen

1. **Reopening into `lane=build` redispatches into the identical wall.** wq#4540 is a build
   ticket; the deploy needs write access to `/home/lumen/minerva/context` and to lumen-bin's
   primary tree, neither of which a BUILD dispatch has (ISSUE-LOG 1527;
   `harness/fleet-exec-policy.yaml:67` excludes `/home/lumen/minerva` by name, wq#1467). A
   reopen that does not also re-lane and re-scope the ticket is a redispatch loop, and once it
   is re-laned and re-scoped it *is* a new ticket wearing an old id.
2. **The build half is real and was reproduced by an independent verifier.** wq#4567 extracted
   `e2a32d90` into a detached worktree and got 9 passed (`harness/tests/test_instincts_render.py`)
   and 14 passed (`ops/wq4540-boot-budget-claude-context/`). Marking that rework distorts
   closure-quality stats against a worker that did its half correctly inside its grants.
3. **The closure was on the record, not a silent false-complete.** wq#4540's notes carry
   `[UNMERGED-PR: ...]` and state plainly that the rest is "staged ... because minerva and bin's
   primary tree are unreachable". `research/TICKET_CLOSURE_AUDIT.md:71-72` draws exactly this
   line: 13 untagged open-PR closes are the F1 false-complete bucket; 6 `[UNMERGED-PR]`-tagged
   ones are "closed on purpose, on the record". wq#4540 is in the second bucket.

So the defect is not a dishonest close. **It is a missing successor.** wq#4537 — the precedent
wq#4567 cites — was deployed only because an orchestrator session with minerva write
(`[DEPLOYED:]`/`[DEPLOY-VERIFY:]` in its notes) happened to be driving that lineage at the time.
wq#4540 was claimed four minutes after wq#4537 closed, staged the same way, and had no such
session behind it. Nothing in the pipeline files the deploy; the staging doctrine ends in mid-air.
That is the thing worth fixing (wq#4577), and it is not fixed by flipping one ticket's status.

## 3. Correcting wq#4567's framing of the follow-up

wq#4567 proposed "a follow-up ticket with minerva + lumen-bin fs grants". Two corrections:

- **No new grant was minted, and none should be.** `/home/lumen/minerva` is off
  `fleet-exec-policy.yaml`'s `read_dirs` deliberately, because credentials are mirrored into
  `context/sections` (wq#1467) — and `claude_context.sh` lives in that same `context/` dir, so
  there is no narrower dir grant than the one wq#1467 refused. The only narrow grant ever made
  under minerva (`/home/lumen/minerva/eval`, wq#1738) required a documented zero-hit credential
  sweep, which `context/` cannot pass by construction. wq#4576 therefore rides an **existing**
  grant: archetype `rca-investigation` already lists `/home/lumen/minerva` in its `add_dirs`
  (`harness/agents.yaml:98-116`, wq#2577). Using a standing grant beats minting one.
- **The guard gap is not the access path.** ISSUE-LOG 1528 records that a
  `/home/lumen/bin/fleet-tools-render` child process read+wrote live minerva files from the
  wq#4540 BUILD session, because guard.sh pattern-matches literal path text in the Bash call and
  does not sandbox a spawned child's I/O. That row is flagged `OPEN — needs a guard.sh change`
  and explicitly "not a usable access path". wq#4576 is told not to ride it.

*Noted, not relitigated:* wq#1467 (minerva ungrantable, credential mirror) and wq#2577 (two
archetypes granted all of `/home/lumen/minerva`) are in tension. wq#2577 justified its grant as
*read* access for diagnosis. Deciding whether that grant should be narrowed to exclude
`context/sections` is a fleet-config question outside this judgment's scope.

## 4. Two defects in the staged bundle, found while scoping the successor

Both are folded into wq#4576's detail as instructions, and logged as ISSUE-LOG row 1542:

1. **`install.sh` step 1 would cause repo/live drift.** `install.sh:26` runs
   `install -m 0755 "$SRC/fleet-tools-render" /home/lumen/bin/fleet-tools-render`, but
   `fleet-tools-render` is a git-tracked file in the lumen-bin repo (`git -C /home/lumen/bin
   ls-files` lists both it and `claude_context.sh`). An out-of-band `install` leaves the live
   file diverged from its repo — the wq#886/wq#1538 class, whose recorded remedy (ISSUE-LOG 239)
   is: land through a lumen-bin PR first, then
   `ops/wq1537-primary-file-deploy-sync/ensure_primary_file_deploy_sync.py --apply --path <file>`.
   wq#4576 must use that route instead of step 1 as written.
2. **Step 4's second sed has an ordering dependency on step 1.** It rewrites
   `FLEET_TOOLS.md` → `FLEET_TOOLS_BOOT.md` inside `claude_context.sh`, and that file only
   exists after `fleet-tools-render --boot` has produced it. Applying step 4 before step 1
   points boot at a missing file.

## 5. Actions taken

| Action | Rationale |
|---|---|
| **wq#4576** [ops, pri 2, deps=4540, archetype `rca-investigation`, sonnet-5@high] | Carries the unrun measurement: land PR #2403 → install steps 2-3 → apply wiring → measure vs 45,000 B → close in wq#4537's `[DEPLOYED:]`/`[DEPLOY-VERIFY:]` shape. `Liveness:` is `grep -q -- 'instincts_render.py --audience boot' /home/lumen/minerva/context/claude_context.sh` — false today, true exactly when the wiring lands, so the premise can fail (wq#2630). |
| **wq#4577** [build, pri 3, deps=4573] | Class fix: refuse `done` when the notes admit a staged-for-another-session deliverable and no successor deps-links the closer. Same refuse-or-record shape as the wq#899 merge-state gate. |
| `wq note 4540` | The closed ticket now states on its face that its probe never ran and names wq#4576 as the carrier. |
| wq#4540 left `done`, PR #2403 left open | Per §2. Landing #2403 is wq#4576's step 1, not a judgment action. |
| ISSUE-LOG row 1537 flipped `escalated` → `resolved` | Records the decision against the escalation that raised it. |

## 6. What would change this ruling

If wq#4576 is not claimed within a day or two, the queue state is wrong in the other direction:
a `done` ticket plus an unworked successor is indistinguishable from the orphan this judgment
set out to fix. In that case reopen wq#4540 in `lane=ops` and fold wq#4576 into it — the status
churn becomes the cheaper error.
