# wq#2940 — verify-pass (archetype 8) of wq#2908: github-mirror-sync blocked by branch-guard pre-push hook

Reviewer: claude-sonnet-5 @ high (fresh instance, same model as author). Worktree
`wq2940-a2`, scratch review clone `/tmp/fworker-wq2940/pr166-review` (lumen-bin,
`origin/main` + fetched `refs/pull/166/head`). Verified 2026-09-28.
Subject: PR http://100.121.17.5:3000/lumen/lumen-bin/pulls/166, landed on lumen-bin
`main` as **2b78fc3** (`fix(mirror): one-way github mirror push, guard-exempt`, Refs
wq#2908). Note: `main`'s current tip 5422dbe is a second, byte-identical (same tree
`adc48ab`) empty commit stacked on 2b78fc3 with the same message — harmless landing
noise, not part of the reviewed diff.

## Verdict

**CONTRADICT.** wq#2908's own fix item 2 (rewrite the mirror push to run from a bare
clone under `/home/lumen/mirrors/<repo>.git`, never from the primary working tree)
independently and completely solves the blocked-push root cause. Fix item 1 (the
`GIT_MIRROR_SYNC=1` exemption added to `.githooks/branch-guard`'s `cmd_push`) is
**dead code that the shipped mirror-sync script never reaches**, and it is not
merely inert: it is a live, unscoped bypass of a hook whose own header explicitly
promises "no env override" (branch-guard line 13, unchanged by this diff). Escalating
per ticket instructions to opus-5@high — filed as **wq#2947**.

## 1. What the fix does (both parts, as landed)

`git show 2b78fc3` (full diff read, not summarized from the PR description):

- `github-mirror-sync` (new file, 75 lines): for each of 6 repos, maintains a bare
  mirror clone at `$MIRROR_ROOT/$name.git` (`git clone --mirror` once, `git fetch
  --prune origin` on later runs), then does
  `GIT_MIRROR_SYNC=1 git -C "$bare" ... push github --mirror --quiet`. Per-repo
  failures log an `aq` row and set `fail=1`; the loop always continues; `exit "$fail"`
  only after every repo is attempted. This part is correct and matches ticket items
  2/3 exactly — confirmed by reading the file directly (`github-mirror-sync:1-76`,
  `/tmp/fworker-wq2940/pr166-review/github-mirror-sync`).
- `.githooks/branch-guard` (`cmd_push`, +8 lines): before the existing protected-branch
  check, added
  ```
  if [ "${GIT_MIRROR_SYNC:-}" = 1 ]; then
    echo "branch-guard: GIT_MIRROR_SYNC=1 — exempting one-way mirror push in $root (wq#2908)" >&2
    exit 0
  fi
  ```
  This is ticket item 1's second option ("key the exemption on an env var the mirror
  unit sets"). Note it exits before the function ever reads `$1`/`$2` (remote name/URL)
  or inspects `remote_ref` — the exemption is **not scoped to the `github` remote or
  to any specific ref**, it unconditionally short-circuits the whole push check.

## 2. The break: the exempted code path is never executed by the shipped script

`cmd_push` only runs as a repo's `pre-push` hook. Branch-guard is wired in via
**common-dir hooks** (`branch-guard install`, `.githooks/branch-guard:104-121`) on the
primary trees (`/home/lumen/bin`, `/home/lumen/foreman`, etc.) — that install path is
never run against `$MIRROR_ROOT/$name.git`.

Confirmed empirically, not by inference:

```
$ git clone --mirror --quiet /home/lumen/bin /tmp/fworker-wq2940/hooktest2.git
$ ls /tmp/fworker-wq2940/hooktest2.git/hooks/
applypatch-msg.sample  commit-msg.sample  fsmonitor-watchman.sample  post-update.sample
pre-applypatch.sample  pre-commit.sample  pre-merge-commit.sample  pre-push.sample
pre-rebase.sample      pre-receive.sample prepare-commit-msg.sample push-to-checkout.sample
sendemail-validate.sample update.sample
```

Every hook is a non-executable `.sample` — `git clone --mirror` of a repo that itself
has branch-guard installed does **not** carry the hook into the bare clone (hooks live
under `.git/hooks`/common-dir, they are not repo content and are not cloned). Also
confirmed no global escape hatch reinstalls it: `git config --global --get
core.hooksPath` and `--system --get core.hooksPath` both exit 1 (unset). And the live
unit that actually runs this script sets no such variable at the process level —
`systemctl cat github-mirror-sync.service` shows only `Environment=HOME=/home/lumen`;
`GIT_MIRROR_SYNC=1` is set **inline on the single push command inside the script**,
not on the service. So the push at `github-mirror-sync:65`
(`GIT_MIRROR_SYNC=1 git -C "$bare" ... push github --mirror`) runs inside a bare clone
that has zero hooks wired — `pre-push` never fires, so branch-guard's new exemption
branch is never reached for its stated purpose. It was already unreachable at ship
time, which is exactly why the wq#2908 root cause (blocked push) is actually fixed —
by item 2 alone, moving the push out of the guarded primary tree entirely, not by
item 1.

## 3. Why this is a regression, not just a no-op

The dead branch is not inert everywhere — it lives in the tracked
`.githooks/branch-guard`, which **is** installed on every guarded primary
(lumen-bin, claude-foreman, minerva, fleet-inventory, mission-control, ccc). Because
the check fires before any remote/ref inspection, **any** `git push` to **any** branch
in **any** of those trees, run while `GIT_MIRROR_SYNC=1` happens to be set in the
calling shell/process's environment, now silently exits 0 — including a direct push to
the repo's own `main`/`master`, which is the exact failure mode wq#1476 exists to catch
(branch-guard's own header: three prior incidents, wq#792/794/927, of a commit landing
straight on main with no branch/PR). branch-guard's top-of-file comment (line 13,
untouched by this diff) states the design intent this fix violates: *"Deterministic, no
LLM, no env override."* Nothing in the current deployment sets `GIT_MIRROR_SYNC=1`
anywhere but the one inline push in the script (verified above), so there is no active
exploit today — but the fix installed a footgun into a documented no-override guard to
solve a problem item 2 had already solved, and the guard's own promise is now false.

## 4. The "done" verification did not exercise this boundary

wq#2908's done-notes: *"Verified live: branch-guard refuses/exempts/refuses across
no-env/1/0; full mirror-sync run against fake origin+github repos replicated main
correctly and logged aq on the one broken repo without aborting the run."* Both checks
are real and both passed, but neither one is capable of catching this: the first tests
`branch-guard push` in isolation with the env var set/unset (proves the mechanism
switches correctly, says nothing about whether the real script ever puts it in front of
that mechanism); the second tests the script's fetch/push/log/exit-code behavior against
fake remotes but not against a hook-guarded target, so it can't observe that the
production push path bypasses hooks entirely regardless of the guard. This is the
"probe can't fail" pattern named in this ticket's own doctrine (wq#2630): the
verification was drawn only from the population the fix was designed around (does the
guard's own if-branch behave when directly invoked?) and never from outside it (does
anything in the real execution path ever reach that if-branch?).

## 5. Scope check on the rest of the PR

- Per-repo `aq` failure logging and continue-on-error / exit-after-all-attempted
  (ticket item 3): correct as shown in §1, matches `github-mirror-sync:36-72`.
- Script tracked via lumen-pr, no longer untracked drift (ticket item 4): confirmed —
  `git show --stat 2b78fc3` shows both files added in a normal PR-landed commit.
- Ticket item 5 (github==origin equality check added to lumen-repo-sweep): **not
  done**, and wq#2908's own done-notes say so explicitly ("Deferred (documented,
  non-blocking): live github==origin equality check in lumen-repo-sweep"). This is an
  honest, disclosed gap, not a new finding — not re-raised here as a contradiction,
  just noted for the record since it's the one ticket item that's still open.
- Also disclosed and not re-litigated: `/home/lumen/bin` primary parked off `main`
  from a prior attempt (ISSUE-LOG row 920), needs an operator `ffprimary`.

## 6. What a correct fix looks like (for the judgment pass, not applied here)

Not implementing — this is a verify-pass. For the opus judgment: the branch-guard
change should either (a) be reverted since item 2 makes it unnecessary, or (b) if kept
as defense-in-depth, be scoped to the actual remote/ref instead of a bare env-var
check — e.g. read `$1` (remote name passed to the pre-push hook) and require it to
equal `github` *and* require the URL to match a configured mirror remote, so an
env var alone can never silence the guard for `origin`/`main`.

## Evidence trail

- PR diff read in full via `git show 2b78fc3` / `git diff main..pr166` in scratch
  clone `/tmp/fworker-wq2940/pr166-review` (lumen-bin `origin` fetched, PR ref
  `refs/pull/166/head` fetched anonymously — no credentials printed, per wq#2940
  prior-attempt critique).
- `github-mirror-sync` and `.githooks/branch-guard` read in full via the Read tool.
- Empirical bare-clone hooks check: `git clone --mirror /home/lumen/bin
  /tmp/fworker-wq2940/hooktest2.git` → hooks dir contains only `*.sample`.
- `git config --global/--system --get core.hooksPath`: both unset.
- `systemctl cat github-mirror-sync.service`: no `GIT_MIRROR_SYNC` at the unit level.
- wq#2908 ticket detail + done-notes read via `/home/lumen/bin/wq show 2908`.
