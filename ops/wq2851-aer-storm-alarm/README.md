# wq#2851 — AER-storm leading-indicator alarm for MacPro6,1 nodes

`aer_storm_alarm.py` polls the four MacPro6,1 nodes (isis, hathor, khepri,
nephthys — the same set as `bin/power-remediate`'s `ELIGIBLE` allowlist and
`ops/fan_floor_alarm.yaml`'s `nodes:` block) every 5 minutes over ssh, counts
kernel `PCIe Bus Error` lines in the last 15 minutes, and raises an
EDGE-triggered `aq` alert (with a `RECOVERED` clear) when the count crosses
`raise_above` (default 30). **Notify only — it never touches power,
`power-remediate`, or any unit.** Rule and node list live in
`aer_storm_alarm.yaml`.

## Why this exists

wq#2776's drop recheck found nephthys losing power abruptly three times
(09-24, 09-25, 09-27), each time with ollama idle (only `/api/ps` probes —
not a load or thermal event) and each loss preceded within seconds by a PCIe
AER storm on root port 00:01.0 (Intel 8086:0e02) and the PLX PEX8723
(10:00.0). `power-remediate` is `ARMED=0`, so nothing cut the plug; the Mac
itself powered off. The FINDING's per-boot hourly `journalctl -k 'PCIe Bus
Error'` counts:

| Boot | Ended | Hourly AER counts | Outcome |
|---|---|---|---|
| -5 | abrupt | 296 / 1986 / 468 / 555 per hour | died |
| -3 | abrupt | 33–114/hr, rising to 69 in the last hour | died |
| -1 | abrupt, 44 min after boot | 870 in its final hour | died |
| 0 | healthy, 22h+ | 8 total, max 5/hr | still up |

## Threshold: `raise_above=30` in a 15-minute window

The FINDING gives **hourly** counts; this alarm evaluates a **15-minute**
sliding window (matching the "leading indicator... within seconds" framing —
a storm that precedes a power loss by seconds is not a rate spread evenly
across the hour it falls in, it is a burst). Reasoning:

- **Healthy ceiling.** Boot 0's worst hour was 5. Spread as evenly as
  possible across four 15-minute quarters, that is ~1.25 per window — nowhere
  near 30. A 30-line/15-min bar sits **6× above** the healthy hourly ceiling
  even under the least favorable (perfectly uniform) distribution of that
  ceiling.
- **Pre-drop floor.** The lowest *hourly* rate recorded in a boot that then
  died is boot -3's 33/hr (rising to 69/hr in its last hour). An AER
  "storm" is a burst by its own name; nothing in the FINDING suggests these
  33–1986 errors arrived one every ~1–2 minutes uniformly — the surrounding
  prose ("preceded within seconds") and the order-of-magnitude spread between
  33/hr and 1986/hr point at concentrated bursts. A 15-minute window sees a
  slice of that burst, not 1/4 of the hour's total, so 30 in 15 minutes is
  reachable well before an hourly rate would need to average 120+/hr.
- **Hysteresis.** `clear_at_or_below` is set to 15 (half of `raise_above`) so
  a count sitting near the boundary does not flap the alarm open/closed every
  5-minute check, the same pattern `ops/fan_floor_alarm.yaml`'s
  `te0t_clear_below_c` uses relative to `te0t_above_c`.

**This is a starting value, not a proven-optimal one** — see the gap below.

## Known gap: this worker could not replay against real retained journals

This ticket was built by a headless build worker with no live `ssh` or
`fleet-exec` grant to isis/hathor/khepri/nephthys (`fleet-exec grant` →
`DENY [grant:grant_missing_env]` in this sandbox). Doctrine says never guess a
number that can be verified — so rather than inventing per-15-minute figures
for nephthys's boots -1/-3/-5 or baselines for hathor/isis/khepri, this
report says plainly what is missing and ships the exact tooling to close the
gap the moment someone with fleet ssh access runs it:

```sh
# replay each retained boot that died, and the healthy control, against the
# SAME sliding-window rule `check` uses live — must fire on -1/-3/-5, must
# NOT fire on boot 0:
ssh lumen@100.117.66.102 'journalctl -k -b -1 -o short-iso --no-pager -g "PCIe Bus Error"' > boot-1.log
ssh lumen@100.117.66.102 'journalctl -k -b -3 -o short-iso --no-pager -g "PCIe Bus Error"' > boot-3.log
ssh lumen@100.117.66.102 'journalctl -k -b -5 -o short-iso --no-pager -g "PCIe Bus Error"' > boot-5.log
ssh lumen@100.117.66.102 'journalctl -k -b  0 -o short-iso --no-pager -g "PCIe Bus Error"' > boot-0.log
python3 aer_storm_alarm.py replay --log boot-1.log --node nephthys
python3 aer_storm_alarm.py replay --log boot-3.log --node nephthys
python3 aer_storm_alarm.py replay --log boot-5.log --node nephthys
python3 aer_storm_alarm.py replay --log boot-0.log --node nephthys   # must print nothing

# 24h hourly baselines for hathor/isis/khepri, so raise_above is not fitted
# to nephthys alone (this alarm's own config carries no node-specific
# threshold today — one number for all four — so this step's job is to
# confirm 30/15min does not also false-positive on the OTHER three under
# ordinary load, not just nephthys under duress):
python3 aer_storm_alarm.py baseline --hours 24
```

`replay` and `baseline` are read-only: no ssh side effect beyond a
`journalctl` read, no `aq` row, no `ntfy` call. Unit tests already exercise
the sliding-window and hysteresis logic against synthetic bursts shaped like
the FINDING's numbers (`tests/test_aer_storm_alarm.py::test_replay_fires_on_a_dense_burst`,
`::test_replay_healthy_boot_never_fires`) — what is missing is the *live*
confirmation against nephthys's actual retained boots and the other three
nodes' actual baselines, which needs a grant this build worker does not have.

## Never an automatic action

`check()` only ever calls `aq.ingest`/resolves an `aq` row and calls
`ntfy-send` (`aer_storm_alarm.py:raise_alert`, `:clear_alert`). It has no
import of, or call into, `power-remediate`, `plug-ctl`, `systemctl`, or any
node-power module — there is nothing here for those to trip over, deliberately
(instinct 3 and the ticket's "notify only, NEVER an automatic action").

## Test to failure

```
python3 -m pytest ops/wq2851-aer-storm-alarm/tests -q   # 15 passed
```

Covers: raise above threshold / no-raise at threshold; hysteresis (a count
between `clear_at_or_below` and `raise_above` while already firing stays
firing, matching `ops/fan_floor_alarm.py`'s `next_state`); a probe failure
carries the prior state rather than reading as a clear; **no per-minute
spam** (two consecutive above-threshold passes produce exactly one `aq` row
and one `ntfy` call, not one per pass); dry-run takes no action at all;
edge-clear resolves the open row and fires exactly one `RECOVERED` ntfy;
config validation rejects an inverted hysteresis band and an empty node
list; and `replay()` against a synthetic dense burst (40 lines inside one
15-minute span, the shape of a real storm) fires, while a synthetic healthy
boot (8 lines spread across 22 hours) never does.

## Install

```
ssh lumen@100.117.66.102 'lumen-run start --root -- bash /home/lumen/foreman/ops/wq2851-aer-storm-alarm/install.sh'
```

Then run the replay/baseline commands above once, before trusting the
threshold unattended.
