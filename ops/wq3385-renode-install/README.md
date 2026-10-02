# wq#3385 — install Renode full-system emulator

Full MCU+peripheral emulation (ESP32/Coral/embedded RE) via
[Renode](https://github.com/renode/renode), installed as the upstream portable
.NET 8 self-contained build rather than apt: a BUILD worker has no root
(ISSUE-LOG #1153 — the same apt-needs-root wall hit wq#3354), and this distro's
apt has no `renode` package anyway. The portable tarball embeds its own .NET
runtime, so it needs neither root nor a system-wide dotnet install.

## What's live

- `/home/lumen/bin/renode-dist/` — Renode v1.17.0 portable build, extracted
  from `renode-1.17.0.linux-portable.tar.gz`
  (`https://github.com/renode/renode/releases/download/v1.17.0/renode-1.17.0.linux-portable.tar.gz`).
  Gitignored in the bin repo (`/renode-dist`, alongside `/trivy` and `/gatus` —
  large downloaded binary, not source).
- `/home/lumen/bin/renode` — thin wrapper, `exec`s `renode-dist/renode "$@"`.

Verified live: `/home/lumen/bin/renode --version` →
`Renode v1.17.0` / `build: 1.17.0+20260907gitf1dd1b4af` / `runtime: .NET 8.0.12`.

`/home/lumen/bin` is a PRIMARY tree (wq#900): a worker may drop fresh files
into it over plain Bash (`cp`/`mv`), which is how the files above actually
landed, but every git branch/commit verb targeting it is denied — so, like
wq#2095's `minerva/context` edit, the live change has no PR of its own. The
exact diff is recorded in `wq3385_bin.diff` (`git diff --cached` in
`/home/lumen/bin`, left staged-but-uncommitted since even `git reset` is
denied there) for the record and for whatever sync-live/deploy-ff process
later reconciles the bin repo's tracked state with what's actually on disk.

## install.sh

Reproduces the above: downloads the pinned `v1.17.0` portable tarball,
extracts it to `renode-dist/` (skips extraction if already present — rerun
is a no-op, not a reinstall), and (re)installs the wrapper. `--verify` is
read-only per `../VERIFY_CONTRACT.md`: checks the wrapper is executable, runs
`renode --version` and checks it reports `Renode v1.17.0`, and checks the
underlying dist binary exists.

```
bash ops/wq3385-renode-install/install.sh           # install/update
bash ops/wq3385-renode-install/install.sh --verify  # read-only check
```

Not verified here: actual MCU/peripheral emulation (ESP32, Coral) — this
ticket is the install step. `renode-test` (also in `renode-dist/`) ships the
Robot-Framework test runner for future emulation-script work.
