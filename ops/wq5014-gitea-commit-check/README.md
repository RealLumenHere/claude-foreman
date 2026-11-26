# wq#5014 - Gitea pre-receive commit policy (commit-check), fail-closed (Anubis)

`hook.py` is a git pre-receive hook. For every branch update it runs
[commit-check](https://github.com/commit-check/commit-check) 2.22.0 (MIT, pure Python, no deps,
no network calls with this config) on each commit the push introduces, against `cchk.toml`:

| Rule | Setting | Why |
|---|---|---|
| Conventional Commits | on, types = lumen-pr `CONV_TYPES` | house §3 |
| subject length | <= 80 | ticket; lumen-pr itself stops at 60 |
| subject capitalised / imperative | off | house style is lowercase; imperative check is a heuristic with false positives |
| WIP / fixup commits | refused | |
| AI attribution | `disclose` | `Assisted-by:`/`Generated-by:` accepted; an AI tool as `Co-authored-by:` or author refused (lumen-pr wq#4798 rule) |
| Signed-off-by | **required** (wq#5138, 2026-11-26) | every commit, merge commits included; see "Sign-off" below |

## Fail-closed
commit-check rc 1 (policy) and rc 2 (cannot run: bad config, git unreadable) both refuse. So does
any hook error: checker missing, timeout (20 s per commit, 120 s per push), exception. This is the
opposite of the wq#2286 secret scan (fails open).

Only commits new to the server are checked (`git rev-list <new> --not --all`), so history that
predates the hook never blocks a push. Skipped: ref deletions, tags, refs outside `refs/heads/`.

**Exempt repos**: `/etc/lumen-commit-check/exempt-repos` (from `exempt-repos`), `owner/name` per
line. Only data-sync repos belong there: `lumen/obsidian-vault` (its git-sync commits
`auto: fleet memory sync <ts>`). fleet-config is code and is NOT exempt; its overnight agents'
plain subjects will be refused and must move to Conventional Commits.

**Break glass** (root on Anubis): `sudo touch /etc/lumen-commit-check/DISABLED`. Every push is
then accepted with a warning and a syslog line (`journalctl -t lumen-commit-check`). `rm` it to
re-arm.

## Replay before enabling (2026-11-19)
Over 60 days of main on foreman/bin/ccc/minerva, 5.4% of commits would have failed, nearly all
`Co-authored-by: Claude Code` from before wq#4798. Over the last 7 days, with this policy:
2/59 (two subjects over 80 chars, neither made by `lumen-pr`).

Anubis replay, all 13 repos, all branches, last 30 days (lib-only install, 2026-11-19): 33/507
fail - claude-foreman 28/415, ccc 3/48, council 1/4, lumen-bin 1/32, others 0. All are real
violations (raw Claude Code `Co-Authored-By: Claude ...`, subjects over 80).

## Deploy
```
cd ops/wq5014-gitea-commit-check
pip download --no-deps --only-binary=:all: commit-check==2.22.0 -d .   # install.sh checks sha256
tar c . -C .. _verify.sh | ssh anubis@100.121.17.5 \
  'rm -rf /tmp/cchk && mkdir /tmp/cchk && tar x -C /tmp/cchk && sudo -n bash /tmp/cchk/install.sh'
```
`--lib-only` installs library + policy without the repo shims. Re-run after creating a repo;
`install.sh --check` lists repos without `hooks/pre-receive.d/60-commit-check`.

## Roll back
`sudo bash install.sh --remove` on Anubis (or delete one repo's `60-commit-check`).

## Sign-off
`require_signed_off_by = true` since 2026-11-26 (wq#5138). Every commit pushed to a branch needs
a `Signed-off-by:` trailer. commit-check does not exempt merge commits from it.

Who writes it:
- `lumen-pr commit` appends `Signed-off-by: Lumen Industries <lumen@lumennode.uk>` after the
  `Assisted-by:` footer (lumen-bin 3ce4ff4b).
- The token-engine broker forwards `Signed-off-by:` from the branch commits into the squash or
  merge body it sends to Gitea, and adds the house sign-off when none is there
  (`FOOTER_RE` / `FOOTER_SIGNOFF`, deployed 2026-11-20 01:11Z). Both `lumen-pr land` and
  `lumen-pr land --merge` therefore land a signed-off commit.
- By hand: `git commit -s`.

Replay before the flip (2026-11-26, Anubis, all 13 repos, all branches, 7 days by committer
date, the installed policy plus the flag): 175 commits, 0 refused by the installed policy, 20
refused for the missing sign-off. All 20 are squash commits on main dated 2026-11-19 13:07Z to
2026-11-20 01:07Z, before the broker redeploy. Since the redeploy: 155 of 155 pass. They are
already on the server, and the hook only checks commits that are new to it.

Not covered, by decision (ticket item 3, "or accept they are refused"):
- A merge made with the Gitea web UI button. Its default message has no sign-off, so the push is
  refused with the wq#5138 text unless the line `Signed-off-by: Name <email>` is typed into the
  merge message box. No commit in the replay window came from the web UI, and a web-UI
  merge-commit was already refused for its non-Conventional subject. To keep the button working
  without typing, add `.gitea/default_merge_message/SQUASH_TEMPLATE.md` (and `MERGE_TEMPLATE.md`)
  carrying the trailer to a repo's default branch.
- The "Update branch" button and a local `git merge main` pushed to a branch: the merge commit
  needs the trailer too (`git merge --signoff`, or rebase). No merge commit was pushed to any
  branch in the replay window.

Rollback (one minute): set `require_signed_off_by = false` in `cchk.toml`, land it, re-run the
deploy below. Immediate, root on Anubis: `sudo touch /etc/lumen-commit-check/DISABLED` (accepts
every push, logs each one), `rm` it to re-arm.

## Tests
`python3 -m pytest test_hook.py` (needs the wheel next to it or `CCHK_WHEEL=`): 22 tests, 20 of
them pushes to a real bare repo. With the hook's refusal neutered, the refusal tests fail; with
`require_signed_off_by = false` the three sign-off tests fail; with the trailer taken out of
install.sh's smoke message the installer test fails.
