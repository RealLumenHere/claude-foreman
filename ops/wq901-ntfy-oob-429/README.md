# wq#901 — ntfy out-of-band backup path 429-rate-limited

## Problem

`ntfy-send` mirrors every `high`/`urgent` publish to the out-of-band leg
(`NTFY_OOB`, an anonymous topic on ntfy.sh) via `NTFY_OOB_PRIORITIES="high,urgent"`.
ntfy.sh rate-limits anonymous publishers per source IP (server defaults: a burst
budget of ~60 requests, refilled at roughly one request per 5 s). Fleet `high`
traffic arrives in bursts, so the visitor budget drains and never recovers:

- Journal since 2026-08-22: **52 of ~70 OOB attempts returned 429** (`delivered
  [primary=200 oob=429(FAIL)]`), 36 of them on 2026-08-23 alone.
- Primary (self-hosted, loopback/tailnet) delivery is unaffected — every one of
  those sends had `primary=200`.

The damage is worse than a noisy log line: mirroring *consumes* the OOB rate
budget during exactly the high-volume windows in which the primary is most
likely to fail. If Lumen died mid-storm, the fallback publish on line 36 of
`ntfy-send` (`primary down -> always try out-of-band`) would hit an
already-exhausted 429 budget. The backup path is dead precisely when it is
needed.

## Options considered

### A. Authenticated ntfy.sh account
Paid tier raises per-visitor limits. Rejected: recurring spend is Frank's call,
it adds a credential to manage on every host that publishes, and it treats the
symptom — we would still be spending OOB budget on traffic the primary already
delivered.

### B. Second self-hosted instance on Bes
Bes already runs the failover stack (wq#589), so operationally cheap. Rejected
on purpose-of-path grounds: the OOB leg exists (per the header comment in
`ntfy-send.conf`) to survive the death of Lumen, Cloudflare, and the tunnel. A
Bes instance is reachable only over the same tailnet/CF surface as the rest of
the fleet — it is a second *primary*, not an out-of-band path. It also adds an
always-on service to patch and monitor for a leg that should almost never fire.

### C. Stop mirroring; OOB fires only on primary failure — **chosen, refined**
Drop `high` from `NTFY_OOB_PRIORITIES` but keep `urgent`:

```
NTFY_OOB_PRIORITIES="urgent"
```

- `high` mirroring was ~all of the volume (9 high vs 3 urgent in the 2-day
  sample) and is redundant: primary delivery is verified (origin 200 asserted
  since the 2026-07-22 CF-302 incident), and any primary failure already forces
  an OOB attempt regardless of priority.
- `urgent` (pager-class, a few per week) stays mirrored as belt-and-suspenders
  against a *silently* wrong primary — the failure class that created this
  config in the first place. At that volume it can never trip the 429 budget.
- Zero new infra, zero spend, and the OOB budget stays full for real failovers.

## Keeping the path honest: daily canary

An unexercised backup rots silently. `ntfy-oob-canary.timer` sends one
`low`-priority `--oob` publish per day (negligible against the rate budget) and
asserts the `oob=200` marker in `ntfy-send`'s output — `ntfy-send` itself exits
0 if *any* endpoint accepted the message, which would let a healthy primary mask
a dead OOB leg. A missing `oob=200` fails the unit and trips its `OnFailure=`
hook, which alerts via the (healthy) primary.

## Rollout

```
sudo -u lumen ops/wq901-ntfy-oob-429/install.sh
```

Idempotent: edits `NTFY_OOB_PRIORITIES` in place, installs and enables the
canary units, fires one canary immediately to prove the path.

## Revisit triggers

- OOB canary 429s at 1/day → ntfy.sh has tightened anonymous limits; escalate
  to option A (authenticated account, Frank approves spend).
- A real primary outage during an alert storm drops OOB sends → the storm
  itself exceeds the budget; option A then, not option B (B still shares the
  failure domain).
