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
| Signed-off-by | **off for now** | see "Sign-off" below |

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
The ticket asks for `require_signed_off_by = true`. It stays off until both:
1. `lumen-pr commit` appends `Signed-off-by:`, and
2. the token-engine broker carries it into the squash body (`FOOTER_RE` in
   `/usr/local/lib/token-engine/token_engine_daemon.py` forwards only
   Refs/Assisted-by/Generated-by/BREAKING CHANGE/Co-authored-by; root-owned, needs a privileged
   redeploy).
Enabling it before then refuses every `lumen-pr land`. Tracked as a follow-up ticket.

## Tests
`python3 -m pytest test_hook.py` (needs the wheel next to it or `CCHK_WHEEL=`): 13 push-level
tests on a real bare repo. With the hook's refusal neutered, the 8 refusal tests fail.
