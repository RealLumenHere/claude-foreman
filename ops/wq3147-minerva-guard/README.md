# wq#3147 lumen-minerva-guard

minerva has no CI workflow and no Gitea runner, and `lumen-pr land` merges a repo
with zero checks on review alone. The wq#2900 sqlite leak ratchet
(`minerva/tests/test_sqlite_leak_ratchet.py`) was red on main from ebfcda4 to
864fe16 and nothing alerted (wq#3146).

`lumen-minerva-guard.timer` is intended-off: the 2026-10-08 subtraction disabled it
(`/home/lumen/scratch/subtraction/timers-disable-list.txt:4`), so nothing runs the guard
on a schedule today. `install.sh` copies the script and units but leaves the timer alone,
and `--verify` checks only the files. Re-enabling is a decision: flip `INTENDED_ON=1` in
`install.sh`, which makes install `enable --now` the timer and `--verify` assert
enabled+active+scheduled. When enabled, the timer (every 6h) or a manual
`systemctl start lumen-minerva-guard.service` runs `minerva_guard.py run`: `git fetch origin
main` in `/home/lumen/minerva`, export the origin/main tree with `git archive` into a
temp dir (the primary checkout's uncommitted edits are never tested), run the whole
tracked suite (188 tests, ~30s), and:

- red: aq row (`source=minerva-guard`, `key=minerva-tests-red`, high) + ntfy, on
  entering red or when the failing set changes;
- green after red: resolve the row, one RECOVERED ntfy;
- guard cannot run (fetch/archive failure, pytest timeout/crash, nothing
  collected): aq row `minerva-guard-broken`, once per streak.

State: `/home/lumen/logs/.lumen-minerva-guard.state.json`.

Pre-land check of a branch (no alerts, exit 0 green / 1 red / 2 could not run):

    python3 /home/lumen/services/lumen-minerva-guard/minerva_guard.py check --rev <branch>

Install: `lumen-run start --root -- bash /home/lumen/foreman/ops/wq3147-minerva-guard/install.sh`
(`install.sh --verify` is the read-only deploy check).

Tests: `python3 -m pytest -q ops/wq3147-minerva-guard/tests`. The last test is the
acceptance probe: minerva pinned at 864fe16 plus one committed unguarded
`sqlite3.connect` must come back RED naming the ratchet, and 864fe16 alone GREEN.
