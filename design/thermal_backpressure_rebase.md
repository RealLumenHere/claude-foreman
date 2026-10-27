# Thermal backpressure, rebased on the live roster (wq#4714)

Supersedes the install wq#3044 asked for. wq#2484 shipped the guard against a fleet that
no longer exists: `ops/thermal_backpressure.yaml` named isis, hathor, nephthys and khepri
(MacPro6,1, retired and offline — Frank 2026-10-22) and read every node's CPU package
temperature from netdata. On 2026-10-27 `thermal_backpressure.py poll --dry-run` ran past
70 s and died at rc=124, and `status` still held the 2026-10-08 state. Installing the timer
as written would have polled four dead hosts every five minutes.

Three questions had to be answered before the timer could go in: what the guard watches,
what it reads the temperature from, and why one dead node could hang the whole poll.

## 1. Why the poll hung

Not a slow node — a serial read with a 30 s ceiling per HTTP call. `read_temps` loops over
nodes one at a time (`ops/thermal_backpressure.py` `read_temps`), and each node costs two
`curl -s -m 30` calls: `resolve_chart` to find the coretemp chart, then
`netdata_window.fetch` for the window. A host that is *off* blackholes the SYN, so each
call burns the full 30 s rather than failing fast the way lumen's own refused connection
does. Four retired Macs = up to 240 s of pure timeout before the first live node is read.
`fan_floor_alarm.check()` fetches from the same four hosts again, doubling it.

So the hang was structural, and it would come back the first time any *live* node went
down. The fix is a per-node ceiling and a total budget, not a shorter curl timeout.

## 2. What the guard watches now

From the live roster (`lumen_state.py current`, probed 2026-10-27 — see
`/tmp`-free rerun via `ops/thermal_backpressure.py poll --dry-run`):

| node | address | role | package temp at probe | disposition |
|---|---|---|---|---|
| lumen | 100.117.66.102 | primary AI orchestration host (T5810) | 65–73 C | hold `lumen, litellm, minerva-db` |
| maat | 100.105.219.34 | Postgres + Vaultwarden | 60 C | hold |
| anubis | 100.121.17.5 | Borg repo + Gitea (OptiPlex 3090) | 37 C | hold |
| sobek | 100.86.156.91 | vault standby, PG replica (N100) | 36 C | hold |
| bes | 100.110.156.91 | Home Assistant + vlogs sink (N100) | 42 C | hold |
| maksoor | 100.81.204.74 | primary RE workstation | 35 C | hold |
| thor | 100.112.151.100 | Frank's workstation | no source | notify only (instinct 2) |

Dropped: isis, hathor, nephthys, khepri. Retired Macs, offline, no sensor to read.

lumen is the node that matters. It runs the dispatch that makes the heat, it sat at 65–73 C
under ordinary load during this ticket, and it is the only node whose `hold_subsystems`
tokens (`litellm`, `minerva-db`) tickets actually declare, so its hold is the only one that
skips real work today. The other five hold on their own node name, which per wq#995 no
ticket declares — a live hold there records the event and skips nothing. That is deliberate:
the row in `transitions` and the aq it opens are the value until a ticket starts declaring
node names.

Thor stays `notify`, enforced by `validate()` by name *and* by address.

## 3. What the sensor source is now

**netdata is not the source any more. The source is the kernel's own hwmon sysfs node,
read locally on lumen and over SSH everywhere else.**

netdata was not merely down on lumen — `systemctl is-active netdata` says `inactive` and
`is-enabled` says `disabled`. It was turned off deliberately, and nothing in
`kb/ISSUE-LOG.md` records why (no row matches netdata + retire/remove/uninstall). It is
still up on four nodes. Probed 2026-10-27, `http://<node>:19999/api/v1/info`:

    lumen    connection refused      maat     HTTP 200
    anubis   HTTP 200                sobek    HTTP 200
    bes      HTTP 200                maksoor  connection refused
                                     thor     connection refused

A netdata-only guard therefore cannot see lumen or maksoor — it would exclude the one node
whose heat the guard exists to throttle. Meanwhile a `glob('/sys/class/hwmon/hwmon*/temp*_label')`
read for `Package id 0` answered on **all six** non-Thor nodes in the same probe, with no
daemon, no package and no port. Thor answers neither (no key for `frank@`, no netdata), which
is consistent: Thor is notify-only and has nothing to actuate.

Keeping netdata in the loop for the four nodes that still run it would mean two sensor
paths, two failure modes and two sets of thresholds for one number. Not worth it. netdata
stays wired to `observe` and `replay` only, which genuinely need *history* that sysfs cannot
give, and only on nodes that still serve it.

### The sustain window moves into the guard's own db

`sustain_min` exists so a transient spike cannot hold a node. netdata supplied that window
for free (`read_mean` asked it for `n+2` points at 60 s). A sysfs read is instantaneous, so
the guard now keeps the window itself: one sample per node per poll in a new `samples`
table, and `window_mean` over the last `sustain_min` of them. `STEP_S` stays 60, which
makes the stored window bit-for-bit the shape `window_mean`/`evaluate` already expect — the
hysteresis rule, its tests and the committed replay fixtures are untouched.

That only works if the poll runs once a minute, so the timer moves from `OnUnitActiveSec=5min`
to `1min`. A sysfs read costs one SSH round trip (~0.3 s, six in parallel), so a minute is
cheap where five netdata windows were not. `hold_ttl_min` stays 20: refreshing a 20-minute
lease every minute is strictly safer than every five.

A gap still reads as *no verdict*: `window_mean` returns None unless the window is fully
covered at exact 60 s spacing, and `next_temp(hot, None, node)` carries the previous state.
A node that goes dark while held stays held until its lease runs out. It never reads as cool.

## 4. One dead node cannot hang a poll

`sample_temps` reads every node **concurrently**, each under `sensor.node_timeout_s`
(default 8 s), with the whole fan-out bounded by `sensor.poll_budget_s` (default 30 s).
A node that times out yields `(None, "timeout after Ns")`, which is a gap, which carries
state. Worst case for the whole sensor layer is now the budget, not the sum of the nodes —
and `validate()` refuses a config whose budget is below a single node's timeout, so the
guarantee cannot be configured away.

The acceptance probe for this is deliberately pointed at a host *outside* the roster
(`192.0.2.1`, RFC 5737, guaranteed unroutable — not a live fleet node that might answer):
a config containing it must still finish inside the budget. A probe drawn only from nodes
that answer could not have failed.

## 5. Fan-floor alarm (wq#2510)

Kept, and gated off. The alarm's signature is MacPro6,1-specific: Te0T above 80 C while
fan1 never leaves the 790 rpm SMC floor (`research/ISIS_POWEROFF_RCA.md:84-105`). Every
node it names is a retired Mac, and no node on the live roster has a Te0T sensor or an SMC
fan floor — a T5810, an OptiPlex and three N100s cannot enter that state. Leaving
`fan_floor: true` would also re-open the hang: `fan_floor_alarm.check()` fetches from those
same four dead hosts on every poll, outside `sample_temps`' budget.

So `fan_floor: false` in `ops/thermal_backpressure.yaml`, with the reason in the comment,
and `ops/fan_floor_alarm.yaml` keeps all four node entries under an explicit `retired: true`
marker rather than losing them. The module, its thresholds, its replay fixture and
`ops/tests/test_wq2510_fan_floor_alarm.py` all stay in the tree and keep passing. If a
MacPro6,1 comes back, re-enabling is two lines: drop `retired` on the node, flip
`fan_floor`. `next_fan(hot, None)` carries state while it is off, so turning it off cannot
strand a node held by it.

## 6. Order of operations

The yaml rebase and the timeout land first; the timer install (`ops/wq2484-thermal-backpressure`)
only after `poll --dry-run` answers for every node inside the budget. The bundle gained the
`--verify` handler the deploy contract requires (`ops/VERIFY_CONTRACT.md`) — it had none,
so `ops-deploy-drift` could never have told anyone whether the timer was live.
