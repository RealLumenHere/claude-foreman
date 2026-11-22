# wq#5295 — verify of wq#5258 (PR 2724): wq2086-presentation-stability --verify

Verdict: **CONFIRMED** (the shipped claim holds). One non-blocking gap, filed as wq#5314.

Diff under test: commit 982c38f3, `ops/wq2086-presentation-stability/install.sh` (lines 25-31)
and `README.md` (the "Timer is intended-off" section). It removes `venabled`/`vactive` for
`lumen-presentation-stability.timer` from the `--verify` block and adds a comment and a README note.

## Claims checked

| Claim | Probe | Result |
|---|---|---|
| Timer is intended-off per the 2026-10-08 subtraction | `grep -n "" /home/lumen/scratch/subtraction/timers-disable-list.txt` and `grep -n presentation .../phase2-results.txt` | line 7 = `lumen-presentation-stability`; phase2 line 7 = `inactive/disabled svc=inactive`. Holds. |
| Live state matches intent | `systemctl is-enabled` / `is-active` on the timer | `disabled` / `inactive`. Holds. |
| `--verify` is live (exit 0) | `liveness.ops_verified('wq2086-presentation-stability')`, the harness's own probe (it runs `bash install.sh --verify`) | `('SATISFIED', 'wq2086-presentation-stability: VERIFY OK')` |
| Remaining checks still compare bytes | `filecmp.cmp(shallow=False)` src vs installed copy, for the .py, .service and .timer | `[True, True, True]` |
| `--verify` can still fail (drift-detection kept) | Copied `_verify.sh` and the bundle to `/tmp/fworker-wq5295/ops`, pointed `liveness.OPS_ROOT` at that copy, then appended a byte to the source | clean = SATISFIED; drifted .py = `STILL_BLOCKED: MISS differs: /home/lumen/bin/minerva-presentation-stability`; drifted .timer = `STILL_BLOCKED: MISS differs: /etc/systemd/system/lumen-presentation-stability.timer`. Still fails on drift. |
| Root install NOT run | live timer is still `disabled`/`inactive` | Holds; the subtraction is intact. |

## Gap (non-blocking, filed wq#5314)

The PR's own reasoning says a root `install.sh` run would undo the subtraction. The diff only
**documents** that hazard. It does not remove it:

- `install.sh:76` still runs `systemctl enable --now lumen-presentation-stability.timer`
  whenever `/etc/systemd/system` is writable.
- `install.sh:85`: the unprivileged branch still prints `systemctl enable --now ...` as a
  "privileged step still owed" and exits 3. This is the same advice the README now says not to follow.
- There is no regression test. The sibling fixes for the same failure class landed later the same day:
  wq#5266 (8cec2566, `IPTABLES_DRIFT_GUARD_ON=0` gates both install and verify, plus
  `ops/tests/test_wq5266_*`) and wq#5267 (e53d280e, plus `ops/tests/test_wq5267_*`). Both ship
  a stronger shape than this one.
- `ops/deploy_triage.py:94-96` still says the timer is "installed and enabled" (wq#3468
  prose from before the subtraction). `check()` only asserts the unit is known (line 356-359),
  not that it is enabled, so this is stale prose and not a live drift signal.

None of these makes the shipped `--verify` change wrong. The gap is that the install path is
still not consistent with the intent. wq#5314 brings wq2086 to the wq#5266 shape.

## Not a contradiction because

The ticket asked for `--verify` to stop failing without harming the box. It is live (SATISFIED),
it still detects file drift, and the intended-off state is evidenced and intact. No escalation.
