# wq#2333 — redeploy the token-engine broker (the third copy of the mask rule)

The wq#2292 word-boundary fix landed in `token-engine/token_engine_daemon.py`
and stopped there. `/usr/local/lib/token-engine/token_engine_daemon.py` — the
root-owned copy the broker actually executes, and the one `lumen-pr` reaches
through `te_client.py` — was still the pre-wq#2292 build, so the fix was true
in the repo and false in production for a day.

That copy is not a noisy page. `read_worktree_file` raises `Deny` on a mask hit
(`token_engine_daemon.py:372-376`), so a PR body carrying a hit fails the verb
and the result is discarded. The bodies that hit were the ~24 open rotation
tickets: a worker cannot write up the unanchored-prefix bug without quoting
`highs_solver`, and the stale rule read that as a token. ISSUE-LOG row 568
predicted exactly this for the third copy; wq#2296 observed it.

## What was done

Redeployed from the canonical clone through the sanctioned root path — detached
`systemd-run` via `lumen-run`, per instinct 5 — rather than copying the file by
hand, so the unit, the policy snapshot, `te_client.py` and the forge configs all
moved together and the install's own EACCES gate replay ran:

    /home/lumen/bin/lumen-run start --root -- \
        bash /home/lumen/foreman/token-engine/install.sh /home/lumen/foreman/token-engine

`install.sh` is idempotent and ends in `systemctl restart token-engine.service`.

## Evidence

Drift probe (`probe_deployed.sh`), before and after:

| | deployed before | deployed after | checkout |
|---|---|---|---|
| bytes | 35126 | 55244 | 55244 |
| sha256 | `2c6a894e…` | `b58254d3…` | `b58254d3…` |
| boundary anchor `(?<![A-Za-z0-9])` | 0 | 1 | 1 |
| wq#2292 markers | 0 | 2 | 2 |
| wq#2298 markers | 0 | 1 | 1 |

The deployed file and the checkout are now the same bytes, so the liveness
condition — both carry the `(?<![A-Za-z0-9])` boundary and the 30-char body
floor on the github rule (`token_engine_daemon.py:87`) — holds by identity.

Body probe (`check_body.py`), the wq#2296 reproduction run against the deployed
module rather than the checkout:

    pre-wq2292 rule : 2 prefix hit(s) ['ghp_', 'ghs_']
    deployed rules  : none
    OK: deployed broker accepts a body the pre-wq2292 copy refused

Two hits under the old rule and zero under the deployed one is the same split
wq#2296 measured, which is what makes the fixture a reproduction rather than a
body that would have passed either way — `check_body.py` fails loudly if the old
rule ever stops firing on it.

## Why the probes live here

Both read the deployed path, not the checkout, because the checkout was never
the thing that was wrong. Re-run `probe_deployed.sh` after any token-engine
change to catch the same drift class; the `bin/mask-secrets` copy of this rule
is the remaining one, ticketed as wq#2313/#2316.
