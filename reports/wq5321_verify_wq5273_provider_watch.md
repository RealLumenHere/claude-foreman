# wq#5321 — verify of wq#5273 (PR #2748, 5bdd3605): provider-watch intended-off --verify

Verdict: **CONFIRMED**. One non-blocking robustness finding, filed as wq#5326.

## What the PR claims
`ops/wq3336-provider-watch/install.sh --verify` asserted venabled/vactive on a timer the
2026-10-08 subtraction (wq#3648) disabled on purpose, so it failed forever and the
ops-root-apply it filed told the operator to run install.sh as root. That would run
`systemctl enable --now` and undo the subtraction. The fix branches on live timer state
(install.sh:32-54) and makes install refuse without `PROVIDER_WATCH_REENABLE=1`
(install.sh:58-65).

## Checks
| Check | Result |
|---|---|
| Intended-off evidence cited in the diff | Holds. `timers-disable-phase2.txt:51` = `provider-watch.timer`; `timer-massacre-phase2.md:64` lists provider-watch under Cost/provider; `phase2b-results.txt:52` = `DISABLE provider-watch.timer: inactive/disabled` |
| Live state | `systemctl is-enabled`: timer `disabled`, service `static`. `is-active`: both `inactive` |
| Live unit files | `diff` of bundle `.timer`/`.service` against `/etc/systemd/system/`: identical |
| Live liveness probe | `liveness.ops_verified('wq3336-provider-watch')` → `SATISFIED … OFF dormant payload differs … VERIFY OK`. The bare `bash install.sh --verify` call is not on the worker allowlist, so I ran the harness's own liveness function, which runs it |
| "timer is the payload's only caller" | grep for `bin/provider-watch` in /etc/systemd/system, ~/.config/systemd, /etc/cron.d, foreman/harness, minerva found only `provider-watch.service`, which is `static` and has no [Install]. I could not check the user crontab because `crontab -l` is not allow-listed |
| PR tests | `python3 -m pytest -q ops/tests/test_wq5273_provider_watch_intended_off.py` → 7 passed |
| Install guard ordering | The REENABLE gate runs before the root check, so a root operator who follows an old ops-root-apply card gets exit 2 and no enable |

## Adversarial probe: inputs outside the PR's tested population
`python3 reports/wq5321_verify_wq5273_probe.py` reuses the PR's hermetic shim harness from
this checkout, so the input is pinned to the committed tree:

```
unreachable-systemctl rc=0 :: OFF provider-watch.timer unknown/unknown (...) | VERIFY OK
enabled-runtime/inactive rc=0 :: OFF ... | VERIFY OK
masked/inactive rc=0 :: OFF ... | VERIFY OK
enabled-runtime/active rc=1 :: MISS provider-watch.timer enabled-runtime but active (...) | VERIFY FAIL (1)
```

- masked/inactive and enabled-runtime/inactive pass. Both are off states that cannot fire, so this is correct.
- enabled-runtime/active fails, as it should. The MISS text says "subtraction bypassed", which is accurate enough.
- **Finding (wq#5326, pri 4):** if systemctl returns nothing (no bus, sandbox), both states are
  empty and fall into the OFF branch, so the result is VERIFY OK. Before the PR, venabled reported
  MISS for the same input, so the new check fails open. The live verdict on Lumen is unaffected
  because the real state is readable and disabled/inactive. The fix is to treat empty state as ERR.

## Not a defect
The commit carries a `Co-authored-by: Lumen Industries` trailer. Every recent commit on main
has the same trailer, so the landing tooling adds it and the author did not.
