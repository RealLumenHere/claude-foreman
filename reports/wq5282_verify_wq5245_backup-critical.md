# wq#5282 — verify wq#5245 (PR #2716, c80fa95c): lumen-backup-critical exit 2

Verdict: **CONFIRMED.** Read the diff cold and tried to break it. The fix is live, it is narrow, and the service has stayed green since it went in.

## Checks (2026-11-22 ~11:55Z)

| # | Attack | Evidence | Result |
|---|---|---|---|
| 1 | Repo copy differs from what actually runs | `sha256sum` of live `/home/lumen/services/backup/scripts/backup-critical.sh` and `ops/wq5245-backup-critical-hidden-corpus/backup-critical.sh`: both `68504e7d…925f` | identical |
| 2 | Service is still failing | `systemctl show`: `Result=success ExecMainStatus=0`, exit 11:06:27Z. Journal for 11-22: `errors=2`/`status=2` through 06:02Z, then `errors=0` at 07:07, 08:04, 09:04, 10:02 and 11:06Z (5 hourly runs in a row) | fixed |
| 3 | Restic still warns and the script hides it | `critical-20261122.log`: 14 snapshots with `could not be read` before the fix (lines 23–358). The 10 snapshots after it (lines 389–563) have no `error:` or `Warning` lines. The script still counts any nonzero restic rc as a failure (fail-closed is kept) | clean |
| 4 | The exclude is too broad and drops real data | Processed-file counts stay at 194k–223k after the fix, against 193k–218k before it, so there is no drop. The pattern is the absolute path `/home/lumen/scratch/gate_corpus/.hidden` (script :259). The public half (`MANIFEST.json`, `cases/`, `run_corpus.py`) sits outside it and is lumen-readable | narrow |
| 5 | Root cause misattributed | `critical-20261117.log` has 0 `could not be read` and 24× `errors=0`. `critical-20261118.log` has 32. Every `^error` line in 11-18 and 11-21 refers to `gate_corpus/.hidden` (non-`.hidden` count: 0). Matches README:22–26 | correct |
| 6 | The exclude loses backup coverage | `.hidden/*` is root 0700/0600 (stat), so lumen-uid restic could never read it. Snapshots taken before the fix already lacked it, so coverage is unchanged | no regression |
| 7 | `rc=$?` in the `else` arm reports the wrong code | Per bash `if` semantics, `$?` at the start of the `else` list is the condition's exit status, so this captures restic's rc. Not run live: no failure has happened since the fix, and `bash <probe>` is not on this lane's allowlist | sound (by spec) |
| 8 | ISSUE-LOG entry | `/home/lumen/kb/ISSUE-LOG.md:1850` row 1701 is present | present |

## Nits (not contradictions)
- README:26 and ISSUE-LOG #1701 call `.hidden` "root:root 0700". In fact the directory itself is `lumen:lumen 0755` (ctime 2026-11-20 05:17Z) and only its contents are root-owned. Restic's behaviour is the same either way. Side note for the gate-corpus owner: because the parent is lumen-writable, any lumen-uid agent can rename or delete the hold-out entries, though it still cannot read them.
- The commit has a `Co-authored-by: Lumen` trailer. All of the last 20 commits on main carry it, so it comes from the tooling, not from this author.
