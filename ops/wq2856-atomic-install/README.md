# wq#2856 — install scripts by rename, never cp-in-place

bash reads a script from its open fd a chunk at a time and seeks back to the end of
each command. `cp new /home/lumen/bin/x` truncates and rewrites x's existing inode, so a
bash already running x (a timer/unit mid-run) resumes on the NEW bytes at the OLD
offset. That is how lumen-re-sync.service failed at 2026-09-28T03:25:42Z ("aq:
Permission denied / LOCK: unbound variable", OnFailure fired) when project-sync-083e27
replaced /home/lumen/bin/re-sync with cp.

`install-script <src> <dst>` writes a temp file beside `<dst>`, chmods it and `mv -f`s it
over: `<dst>` gets a new inode, the running process keeps the old one.

## What changed

- `install-script` (this dir): the helper. Installed to `/home/lumen/bin/install-script`.
- `harness/guard.sh`: worker sessions are denied `cp …`/`> …` onto an existing regular
  file under `/home/lumen/bin` (symlinks resolved; `-t DIR` and dir destinations
  handled). New files, `.bak` copies, `cp x.tmp && mv`, `>>` stay allowed.
- Every foreman `ops/*` installer that cp'd into `/home/lumen/bin` now calls
  `ops/wq2856-atomic-install/install-script` via its repo-relative path:
  wq980, wq1184 (x2), wq1298, wq1658, wq2086 (install + rollback), wq2316.

Already safe, left alone: `lumen-pr land`'s `git merge --ff-only` (git checkout writes
a new inode per changed file — `test_git_ff_merge_lets_running_script_finish_old_bytes`
pins that), coreutils `install`
(unlinks first), `sed -i` (renames), `reports/opsaudit_a13_*` (already `.new` + `mv`).

## Install

    bash ops/wq2856-atomic-install/install.sh --dry-run
    bash ops/wq2856-atomic-install/install.sh

## Instinct candidate (proposal only, for Frank's review — not written to instincts.yaml)

> Never `cp` over a script a timer/unit may be executing — rename it in
> (`/home/lumen/bin/install-script <src> <dst>`).

The guard wall covers worker sessions only; the instinct would cover interactive
sessions and other nodes.

## Tests

    python3 -m pytest -q ops/wq2856-atomic-install/test_install_script.py
    python3 -m pytest -q harness/tests/test_guard.py -k bin_

`test_cp_in_place_hijacks_running_script` is the control: the same race run with cp
prints `HIJACKED`. The install-script case must print `OLD`.
