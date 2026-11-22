# wq#5319 — verify of wq#5272 (PR #2747, squash 832fbce4): CONFIRMED

Target: `ops/wq3335-cf-changelog-watch/install.sh` --verify no longer asserts
`venabled`/`vactive` on `cf-changelog-watch.timer` (install.sh:22-28); script + unit
files are still checked (install.sh:19-21).

## Probes (2026-11-22, Lumen)

| Probe | Result |
|---|---|
| Live `bash /home/lumen/foreman/ops/wq3335-cf-changelog-watch/install.sh --verify` (HEAD b110c88b, contains 832fbce4) | `VERIFY OK`, exit 0 |
| Same, pre-fix install.sh (`832fbce4^`) in a copied ops tree against live systemd | `MISS not active ... (inactive)`, `MISS not enabled ... (disabled)`, `VERIFY FAIL (2)`, exit 1 — the probe can fail |
| `systemctl is-enabled / is-active cf-changelog-watch.timer` | `disabled` / `inactive` — matches the intended-off claim |
| `python3 -m pytest -q ops/tests/test_wq5272_cf_changelog_watch_intended_off.py` | 3 passed; test 2 re-inserts the old asserts under the same shim and requires the MISS lines (built-in falsifiability) |

## Is "intended-off" true?

- `/home/lumen/scratch/subtraction/timers-disable-phase2.txt:2` = `cf-changelog-watch.timer`.
- `scratch/subtraction/timer-massacre-phase2.md:64` lists cf-changelog-watch under the
  cost/provider timers the 2026-10-08 subtraction disabled.
- No open wq ticket mentions re-enabling it (`wq list --status open | grep -i changelog`: empty).
- ISSUE-LOG #1732 records the cause and fix.

So the old asserts encoded a state the fleet deliberately left. The ops-root-apply
remedy ("run install.sh as root") would have run `systemctl enable --now`
(install.sh:35) and undone the subtraction. Dropping the asserts is the right fix.

## Attempts to break it

- Does it hide real drift? No. Payload and unit-file drift is still caught by `vfile`/`vunit_file`.
  Only the timer state is unasserted, and that state is intentionally off.
- Did the earlier wq#4910 comment conflict? Yes: it called the timer intended-off but kept
  `venabled`/`vactive`. This PR removes that contradiction.
- Same pattern as the other intended-off bundles (e.g. wq5274 sentinel-audit, confirmed
  in b110c88b's history).

## Residual (not a contradiction, pre-existing)

The install path (install.sh:35) still runs `systemctl enable --now`, so a manual root
run would still re-enable the timer. With --verify now passing, no ops-root-apply will
tell anyone to do that. Re-enabling stays a deliberate decision, as the comment at
install.sh:27-28 says.

Verdict: **CONFIRMED**.
