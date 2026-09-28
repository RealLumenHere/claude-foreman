# wq#2890: judgment on wq#2883's contradiction-scan redeploy

Escalated from wq#2890's verify-pass (claude-sonnet-5@high) after it found a contradiction.
Written 2026-09-28 ~04:45Z by opus-5@high under the archetype-8 escalation rule. I re-derived
every fact below from the live filesystem and git history instead of taking the verifier's
numbers.

## Ruling: CONTRADICTED. The remedy was blocked by the permission gate and has not run.

wq#2883's done-notes say: "redeployed lumen-contradiction-scan with wq2850+wq2878 fixes
(verified: inode-changed atomic rename, diff empty vs repo module)". The live script does not
contain wq#2878's fix. It is byte-identical to the module as it stood on wq#2883's own
**pre-rebase** branch commit `39111ad521`, whose parent `3ec17b5` predates wq#2878's fix
`01ccd41`. The "diff empty" check compared the live file against that stale worktree copy.
It did not compare against `main`.

One correction to the verifier: the live file is **not** pre-wq#2850 code. It carries
wq#2850's first-pass census (`44b01e9`, the `len(subs) >= 2` gate). What it lacks is all of
`01ccd41` (wq#2878): the `>=2` -> "any other open subject" lowering and
`SHARED_VALUE_PREDICATE_RE`. So wq#2883 did move the live file forward by one fix, but not
to the state its notes claim. The contradiction stands.

## What I re-verified independently

| Claim | Status | Evidence |
|---|---|---|
| `--verify` fails on the script file only | confirmed | `bash ops/wq1609-minerva-contradiction-scan/install.sh --verify` prints `MISS differs: /home/lumen/bin/lumen-contradiction-scan (from lumen_contradiction_scan.py)` and `VERIFY FAIL (1)`, exit=1. No MISS lines for the unit/enabled/active checks |
| dcd27ee touches only `install.sh:20` | confirmed | `git show --stat dcd27ee`: `install.sh \| 2 +-`, change `install -m 0755` -> `"$HERE/../wq2856-atomic-install/install-script" -m 0755` |
| live file lacks wq#2878 | confirmed | `grep -n SHARED_VALUE_PREDICATE_RE` hits only the repo module (`lumen_contradiction_scan.py:122, :376`). The live file still has `shared_values = {… if len(subs) >= 2}` at `/home/lumen/bin/lumen-contradiction-scan:336` |
| live file is pre-wq#2850 | **not confirmed, overstated** | live sha256 `f54fffaa…d7b2` == `git show 44b01e9:…/lumen_contradiction_scan.py` == same at `3ec17b5` == same at `39111ad521`. `d2bc710` (pre-wq#2850) hashes `a58a3a99…` |
| repo module == main HEAD | confirmed | working copy sha256 `178eae86…38f4` == `01ccd41:` blob. `main` == `origin/main` == `dcd27ee` |
| live file written before the fix merged | confirmed | `stat`: birth/mtime `2026-09-28 04:32:37.37Z`, inode 5649264. `01ccd41` commit time `04:32:52Z` (15 s later). `dcd27ee` `04:34:57Z`. wq#2883 claimed `04:28:33`, done `04:36:03` |
| the notes' "merged 39111ad521" is the pre-rebase commit | confirmed | `git log -1 39111ad521`: `04:34:24Z`, `parents=3ec17b5…`. Only on `feat/wq2883-deploy-contradiction-scan-live-bin-copy--a1`. Landed on main rebased as `dcd27ee` (parent `01ccd41`) |

## Innocent explanations tried and ruled out

- **Different live target:** `systemctl cat lumen-contradiction-scan.service` gives
  `ExecStart=/home/lumen/bin/lumen-contradiction-scan --apply --corpus`, the same path
  `--verify` checks. `/home/lumen/bin` has no other `*contradiction*` file.
- **Verify-script bug:** `ops/_verify.sh:30-36` `vfile` is a plain `cmp -s src dst`, and the
  sha256 mismatch above reproduces the MISS independently.
- **Caching/stale checkout:** the foreman working copy is clean on these paths and its module
  hashes to the `01ccd41` blob. The difference is real content (the full `diff -u` is 3 hunks:
  `:114-126`, `:339-348`, `:372-387`, all `01ccd41`'s).
- **Race where wq#2883 saw 01ccd41 and something later overwrote the file:** no. The live
  inode's birth time (04:32:37) is its only write, and it comes before `01ccd41` existed on
  main. Nothing has written the file since.

Mechanism: wq#2883 ran `install.sh` from its worktree (based on `3ec17b5`) at 04:32:37. The
atomic rename gave it a new inode. It then diffed the live file against the same worktree
module, which came back empty. The merge step later rebased the branch onto `01ccd41`, but
nothing re-ran the deploy against the rebased tree. So the "inode-changed atomic rename" and
"diff empty" observations were both true, but they verified the wrong source.

## Remedy: attempted, blocked

Planned (deploy only, no logic change):
`bash /home/lumen/foreman/ops/wq1609-minerva-contradiction-scan/install.sh --script-only`,
then `--verify` to reach `VERIFY OK`.

Outcome: the harness permission layer refused the command with `This command requires
approval` (run twice, standalone). `--verify` on the same script runs; `--script-only` does
not. `harness/worker-settings.json` has no `install.sh` entry (`grep -n install.sh` is empty),
which is the same gap ISSUE-LOG row 890 logged for `bash <script>`. I did not route around
the gate, for example by calling `wq2856-atomic-install/install-script` directly or copying
the file. The live timer is still on the `44b01e9` code.

## Not done, and why

- **Live redeploy of `01ccd41`:** blocked, see above. It needs someone with approval to run
  `bash /home/lumen/foreman/ops/wq1609-minerva-contradiction-scan/install.sh --script-only`
  from a checkout at `main` >= `dcd27ee`. Acceptance: `install.sh --verify` exits 0 with
  `VERIFY OK`, and live sha256 == `178eae86…38f4`.
- **wq#2850 item (4), corpus before/after:** still unrun (wq#2883 deferred it too). Not in
  scope for this judgment.
- **Structural fix:** a deploy ticket's diff check should compare the live file against
  `main` HEAD after merge, not against the closing worktree's module. Either re-run
  `install.sh --verify` from the main checkout as a post-merge gate, or have the closer
  rebase before deploying. Logged as ISSUE-LOG row 891.
