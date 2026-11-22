# wq#2243 — credentials embedded in git remote URLs

`simpill-pr-api` (remote `lumenfork`, under simpill-scratch-preserved) stored a
RealLumenHere GitHub PAT in its remote URL; listing remotes printed it into a
session transcript. Treat that token as burned.

## What this module does

`remote_credential_sweep.py scan [--root DIR] [--fix] [--notify]`

- Walks the whole root at any depth (the old `lumen-secret-check` stopped at
  `find -maxdepth 4`), resolves `.git` files for linked worktrees/submodules to
  the shared config, and includes bare repos.
- Flags any http(s)/ftp remote `url`/`pushurl` with userinfo (`token@`,
  `user:pass@`) regardless of token format, plus known token prefixes
  (`ghp_`, `github_pat_`, `gho_`, `ghs_`, `ghu_`, `glpat-`) anywhere in the URL.
  A prefix counts only at a non-alphanumeric boundary with a token-length body,
  so `highs_solver.git` and `ghp_notes.git` are not tokens.
  `ssh://git@` and `git@host:path` are not flagged (username only).
- Values are decoded like git does (quotes, `\"`, `#`/`;` comments): git quotes
  any value with `#` or `;`, so `url = "https://u:pw#1@host/"` is still flagged.
- Userinfo ends at the last `@` before the first `/`, so a raw `@` in a
  password leaves no fragment. Also parsed: `[remote "x"] url = ...` on one
  line, and `[url "https://tok@host/"] insteadOf = ...` (the credential is in
  the section header; reported as `remote=url#N key=url-section`).
- `--fix` rewrites each hit to the token-free URL by editing the config line
  as text (the token never enters an argv); a repeated `url`/`pushurl` under one
  remote is fixed entry by entry and clean siblings are left alone. Fetch/push
  then go through the credential helper the other clones use. A
  `ghp_`/`github_pat_`/... token in the query or fragment is dropped with its
  param. A token in the path cannot be stripped: it is reported as
  `UNSTRIPPABLE` and left in place; `--notify` pages it once (worded "cannot
  auto-strip", state in `--state`, default
  `~/.local/state/remote-credential-sweep/paged-unstrippable`) and again only if
  it clears and recurs or a new one appears. Exit stays 1 while it remains.
- Output is `STATUS config-path remote=NAME key=url|pushurl|url-section`
  (STATUS = FIXED, EMBEDDED, UNSTRIPPABLE). The URL is never printed.
  `--notify` pages ntfy once per run with repo paths only, for URLs that stay
  embedded and for FIXED ones (the token stays valid until rotated).
- Exit 1 only while something remains embedded.

`install.sh` installs an hourly `remote-credential-sweep.timer` running
`scan --fix --notify` from the foreman checkout.

The timer is intended-off: the 2026-10-08 subtraction disabled it
(`/home/lumen/scratch/subtraction/timers-disable-list.txt:82`), and bringing it
back is a pending proposal (`credential-consolidation.md` section 3 item 2), not
a heal. So `install.sh --verify` checks only the deployed unit files, not
enabled/active (wq#5261). Do not run `install.sh` as root to "fix" a red verify:
its `systemctl enable --now` would undo the subtraction. Whoever decides to
re-enable it adds `venabled`/`vactive`/`vtimer_scheduled` back to `--verify`.

## Ticket steps

| step | state |
|---|---|
| (3)/(4) sweep + regression guard | this module + tests (`python3 -m unittest discover -s ops/wq2243-remote-credential-sweep`) |
| (2) strip the clone's URL | `scan --fix` (timer, or run once by hand) |
| (1) rotate the token on GitHub | physical: needs the RealLumenHere account owner; revoke the old PAT, mint a new one into the secrets store (0600 file behind the git credential helper), never into a URL |

Follow-up (bin is a primary tree, read-only to workers): have
`/home/lumen/bin/lumen-secret-check` also call this script, or retire it in
favour of the timer.
