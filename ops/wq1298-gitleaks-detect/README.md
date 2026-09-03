# gitleaks-detect (wq#1298)

Full `gitleaks detect` (scans git history, not just the working tree) across
every fleet repo, on a cron timer — detect-and-alert only, no pre-commit
hooks.

## Why cron, not a pre-commit hook

A blocking pre-commit hook wedges an autonomous agent the moment gitleaks
throws a false positive: the only escape is `--no-verify`, which teaches the
agent to bypass the check permanently (Opus review,
`kb/tooling-findings-review.md` + `kb/fleet-upgrade-plan-review.md`). Cron
instead: runs on a timer, never blocks a commit, and alerts on **new**
findings only (transition-based — see `kb/ISSUE-LOG.md` #10).

## Relationship to lumen-secret-check

`bin/lumen-secret-check` (already cron'd, one of the four Day-0 detectors)
greps `git config` remote URLs for a handful of known PAT prefixes
(`ghp_`, `github_pat_`, etc.) — cheap and already caught the wq#1 leak. This
is broader and heavier: gitleaks' ~150 rules scan full git history, catching
secrets that were committed and later "removed" (still live in history) as
well as anything outside that narrow prefix list. Run both; neither
subsumes the other.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `gitleaks_detect.py` | `/home/lumen/bin/gitleaks-detect-cron` | scans every fleet repo, alerts on new findings |
| `gitleaks-detect.service` | `/etc/systemd/system/gitleaks-detect.service` | oneshot runner |
| `gitleaks-detect.timer` | `/etc/systemd/system/gitleaks-detect.timer` | daily cadence, boot+15min, ±10min jitter |
| `install.sh` | run in place | builds gitleaks from source, deploys the above, enables the timer |

## How it works

1. Walks `/home/lumen` (maxdepth 4, skipping `node_modules`/`.venv`/etc.) for
   every dir containing a `.git` (dir or file — worktrees included).
2. For each repo, runs `gitleaks detect --source <repo> --report-format json
   --exit-code 0` (full history scan; exit 0 is forced so "leaks found"
   isn't treated as a tool failure).
3. Diffs each finding's `Fingerprint` against the previous run's state
   (`/home/lumen/logs/.gitleaks-detect.state.json`). Only fingerprints not
   seen before trigger an alert — a standing leak pages once, not every day.
4. Alerts via `ntfy-send` with rule/file/line/commit/fingerprint. **Never**
   the raw secret value (instinct 3, mask before print) — gitleaks' `Secret`
   and `Match` fields are stripped before anything reaches the alert path.
5. Stamps `/home/lumen/logs/.stamp.gitleaks-detect` on completion (dead-man
   coverage via `ops/wq1184-gatus-health-checks/cron_deadman.py`, opt-in by
   stamp-file existence).

If the `gitleaks` binary is missing, the run alerts once and exits 0 rather
than failing the service (so a missed `install.sh` step pages instead of
red-looping the timer).

## Bridge safety

The only network call this script makes at cron time is to `ntfy-send`. It
never probes routers or the bridge, and never installs or pulls anything —
`gitleaks` itself is built once, out of band, by `install.sh`, not on every
cron run.

## Install

```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq1298-gitleaks-detect/install.sh
```

Manual run: `systemctl start gitleaks-detect.service`
Dry run (no alerts sent, no state written):
`python3 /home/lumen/bin/gitleaks-detect-cron run --dry-run`
Logs: `journalctl -u gitleaks-detect.service -n 100 --no-pager`

## Tests

`python3 ops/wq1298-gitleaks-detect/test_gitleaks_detect.py` — offline,
monkeypatches `subprocess.run` for both gitleaks and ntfy-send, no real
binary or network required. Covers: repo discovery (incl. worktrees, incl.
excluding `node_modules`), gitleaks JSON report parsing, tool-failure vs.
findings-found distinction, transition-based alerting (new-only, no repeat
alerts on unchanged findings), the redact step never leaking a raw secret,
lock contention skipping a run, and the missing-binary alert path.
