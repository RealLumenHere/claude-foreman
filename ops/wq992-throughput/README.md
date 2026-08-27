# wq#992 throughput metric — tickets/hour

Answers the production question the rapid-chaining work exists to move: **how many
tickets/hour is the fleet closing, versus the ~7/hr Frank hits by hand?** Up = good.
This is the deliverable-4 metric for wq#992 (rapid chaining A/B/C).

## Why it's a sibling, not part of the R4 ledger

The R4 intervention ledger (`ops/intervention_ledger.py`) is deliberately one number:
Frank-intervention events per week, **down = good**. Throughput is the opposite polarity
(**up = good**) and a different question (production rate, not human dependence). Bolting
it into the ledger would muddy that one-number thesis, so it lives here and emits its own
telemetry. It's meant to **ride alongside** R4 on Glance the same way `parked` and
`undetected downtime` ride alongside the ledger headline.

## Usage

    python3 throughput.py show    # print headline, no writes
    python3 throughput.py run     # compute + emit telemetry JSON

Read-only against `/home/lumen/agents/work-queue/queue.db`. Writes only:
- `/home/lumen/telemetry/throughput.json` (source of record)
- `/home/lumen/glance/assets/throughput.json` (best-effort Glance ride-along)

## The number

`headline_per_hour` = tickets closed in the trailing 24h / 24. 24h smooths spawn bursts
while staying recent enough to reflect the current dispatcher config. `bar_to_beat_per_hour`
= 7.0 (Frank's observed ~40 tickets / 6h manual rate). `windows` also carries 1h/6h/7d
rates, `peak_hour_24h` the demonstrated burst ceiling, and `lanes_24h` the per-lane split.

Baseline at ship (pre rapid-chaining effect): ~0.9 tickets/hr, ~13% of the bar. When
wq#992 B (immediate slot refill) and C (done-watch) are live, this is the number that
should climb toward the bar.

## R4 ride-along hook (for whoever owns intervention_ledger.py)

To surface throughput as a secondary line next to the ledger headline, read the emitted
JSON — no import coupling needed:

    import json
    tp = json.load(open("/home/lumen/telemetry/throughput.json"))
    secondary["throughput_per_hour"] = tp["headline_per_hour"]

## Optional daily emit

`show`/`run` are on-demand. For a passive daily refresh, a timer can call `run` (mirrors
`lumen-intervention-ledger.timer`). Not installed by this change — enable if wanted:

    # ~/.config/systemd/user or system unit calling:
    # /usr/bin/python3 /home/lumen/foreman/ops/wq992-throughput/throughput.py run
