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
  `ssh://git@` and `git@host:path` are not flagged (username only).
- `--fix` rewrites each hit to the token-free URL via `git config --file`;
  fetch/push then go through the credential helper the other clones use.
- Output is `STATUS config-path remote=NAME key=url|pushurl` only. The URL is
  never printed. `--notify` pages ntfy with repo paths only.
- Exit 1 only while something remains embedded.

`install.sh` installs an hourly `remote-credential-sweep.timer` running
`scan --fix --notify` from the foreman checkout.

## Ticket steps

| step | state |
|---|---|
| (3)/(4) sweep + regression guard | this module + tests (`python3 -m unittest discover -s ops/wq2243-remote-credential-sweep`) |
| (2) strip the clone's URL | `scan --fix` (timer, or run once by hand) |
| (1) rotate the token on GitHub | physical: needs the RealLumenHere account owner; revoke the old PAT, mint a new one into the secrets store (0600 file behind the git credential helper), never into a URL |

Follow-up (bin is a primary tree, read-only to workers): have
`/home/lumen/bin/lumen-secret-check` also call this script, or retire it in
favour of the timer.
