# wq#2186 — live probe of the on-disk fleet-exec broker: `restart ollama@isis`

Split out of wq#1481. Run 2026-09-20 by fworker-wq2186 (grant: `restart ollama@isis`, attempt 1, ticket 2186).

Verdict: **PROVEN for the restart. Holddown-cleared check is INDIRECT only** (see below).

## Command

`/home/lumen/bin/fleet-exec restart isis ollama` (exit 0, no stdout/stderr). Broker on disk is `/home/lumen/bin/fleet-exec`, last commit `d327b5c` (slice E, "privileged restart, Lumen-side holddown, verify before/after").

## Audit rows (`foreman/events/fleet-exec.jsonl`, lines 325-326)

| Field | Line 325 (pre-action) | Line 326 (post-verify) |
|---|---|---|
| decision / rule | `allow` / `policy:restart_allowed` | `allow` / `policy:restart_allowed` |
| ticket / worker / node | `2186` / `fworker-wq2186` / `isis` | same |
| grant_sha | `3848f398bf382075167e322888154f052e70bebecb2014fe1b6badf701fd9c40` (joins grant.json) | same |
| before | `active/running`, enter_monotonic `16660271` | same |
| after | not yet | `active/running`, enter_monotonic `7186843890` (unit really restarted) |
| verified / exit / elapsed_s | n/a | `true` / `0` / `1.237` |

`active_enter_monotonic` changed between before and after, so the unit was restarted, not merely found active. Line 324 (wq#1481, `grant:grant_missing_env`) is the earlier deny this ticket exists to supersede.

## Holddown

Broker code path: on `verified`, `fleet-exec:428-430` calls `holddown_drop("isis","ollama")`, key `ollama@isis` (`fleet-exec:250-253`, `275-279`). It ran, since row 326 has `verified: true` and the branch at 428 is the only exit before `sys.exit(0)`. The un-verified path (`fleet-exec:431-438`) is the only one that leaves the flag, and it prints `LEFT ACTIVE`; that string was not emitted.

Not directly confirmed: `holddown check ollama@isis` is not on the worker Bash allowlist and `/home/lumen/agents/holddowns/` is outside the worker's allowed dirs (both refused, not retried). `holddown_drop` swallows `OSError`/timeout (`fleet-exec:277-279`), so a failed drop would be silent. A read of `agents/holddowns/ollama@isis.json` (or `holddown check ollama@isis`) from a non-worker session closes this.
