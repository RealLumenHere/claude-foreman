# wq#5306 — verify of wq#5261 (PR #2733, merge e3750105)

Verdict: **CONFIRMED.** No contradiction, no escalation.

## What the diff does

`ops/wq2243-remote-credential-sweep/install.sh` `--verify` drops `venabled`/`vactive`
for `remote-credential-sweep.timer` and keeps the two `vunit_file` checks
(install.sh:12-13 at e3750105). The README gains a paragraph saying the timer is
intended-off and the script must not be root-run to "fix" a red verify.

## Claims checked (2026-11-22 13:3x UTC)

| Claim | Check | Result |
|---|---|---|
| Timer disabled by the 2026-10-08 subtraction | `grep -n credential scratch/subtraction/timers-disable-list.txt phase2-results.txt` | line 82 in both: holds |
| Re-enabling it is a pending Frank proposal | `scratch/subtraction/credential-consolidation.md:51-54` | "Proposed, needs Frank's yes", item 2 = weekly scan replacing the lumen-secret-check cron: holds |
| Unit files byte-match /etc | `diff <(systemctl cat X \| grep -v '^# /etc') ops/.../X` for .service and .timer | both exit 0: holds |
| Timer still off (nobody root-applied) | `systemctl is-enabled/is-active` | disabled / inactive (timer and service): holds |
| Change is on main | `git merge-base --is-ancestor e3750105 origin/main` | 0; no later commits touch the bundle or `ops/_verify.sh` |
| Live verify | `install.sh --verify` (run via python3 subprocess) | `VERIFY OK`, exit 0 |
| Liveness | `liveness.ops_verified('wq2243-remote-credential-sweep')` | `('SATISFIED', '...: VERIFY OK')` |

## Attempts to break it

The risk in dropping checks is a verify that can't fail any more. Probe
`wq5306_probe_wq5261.py` (next to this report) rebuilds the bundle plus
`ops/_verify.sh` from `git show e3750105:<path>` in a temp dir, so the input is pinned,
and runs `--verify` three ways:

```
none       exit=0 want=0 PASS :: VERIFY OK
.service   exit=1 want=1 PASS :: VERIFY FAIL (1)
.timer     exit=1 want=1 PASS :: VERIFY FAIL (1)
```

Drift in either unit file still turns it red. The verify still fails closed on
the part it is meant to own.

## Notes (not defects in this diff)

1. The non-verify install path still runs `systemctl enable --now`. A root run would
   undo the subtraction. The README now warns about this. This is the same as the
   wq#5255 pattern and is acceptable while the timer's return waits on Frank.
2. The harness filed a second ops-root-apply (wq#5305) after wq#5261 closed, even though
   wq#5261's evidence carried `no_deploy_needed`. wq#5305 was closed correctly without
   running anything as root. The filer treating `no_deploy_needed` as a deploy request is
   a harness gap, outside the scope of this diff.
