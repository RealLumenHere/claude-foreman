# wq#1593 — rung retirement needs a price-delta check when it moves a top workload

D7 from `foreman/research/FLEET_COST_PLAN.md`: `mistral-small` was removed
from `litellm_config.yaml` (last spend 2026-08-29 21:00) but carried $8.79 in
the 14-day window at $0.106/M measured input, the cheapest rung on the
board. LPP moved onto `deepseek-v4-flash` at $0.208/M input — roughly
doubling the input rate on the fleet's biggest workload — with no cost
review anywhere in the ticket that did it. Section 6, item 8 names the class
fix: a rung retirement that touches a workload above a spend threshold
should require the price delta in the ticket.

## What's here

- `rung_retirement_price_check.py` — pure decision functions
  (`removed_rungs`, `high_spend_retirements`, `has_price_delta_disclosure`,
  `check_retirement`) plus a thin CLI. Given the model_list before/after a
  retirement, a per-rung spend export, and the ticket body text, it refuses
  (exit 1, one `REFUSED:` line per violation) unless every retired rung that
  cleared the spend threshold has its old-vs-new `$x/M` input price stated
  in the ticket.
- `tests/` — unit tests for every pure function, run standalone (no yaml
  config or spend export needed):
  `python3 ops/wq1593-rung-retirement-price-check/tests/test_rung_retirement_price_check.py`

## Why a pre-flight check, not a runtime callback

Contrast `ops/wq1590-deepseek-connect-guard`, which is wired into the live
LiteLLM proxy as a success/failure callback: that guard watches traffic as
it happens. There is no equivalent hook point here — a rung retirement is an
edit to `/home/lumen/agent/litellm_config.yaml` on the gateway node, outside
this repo and outside this sandbox's write access (same convention noted in
`ops/wq1591-glm53flash-pricing/README.md`), so there is no diff for CI to
gate. This is instead a pre-flight check meant to run by hand — or from a
future `wq add`/`wq done` wrapper — before a rung-retirement ticket is filed
or dispatched.

## Usage

```
python3 rung_retirement_price_check.py \
    --old-config old_model_list.yaml --new-config new_model_list.yaml \
    --spend-json spend_by_rung.json --ticket-body ticket.txt \
    [--threshold-usd 5.0]
```

- `--old-config`/`--new-config`: yaml files with a top-level `model_list:`,
  same shape as `ops/wq1591-glm53flash-pricing/litellm_config_snippet.yaml`
  (a `model_name` per rung). Only presence/absence of `model_name` is used —
  reuse the gateway's actual before/after `model_list` or a trimmed
  extract of it.
- `--spend-json`: `{"rung-name": dollars, ...}` per-rung spend over the
  measurement window, same numbers as FLEET_COST_PLAN.md section 1's
  per-rung table.
- `--ticket-body`: path to the draft ticket text (`-` for stdin).
- `--threshold-usd`: review threshold, default $5.00 — set below D7's $8.79
  so the mistral-small retirement itself would have tripped it.

Exits 0 and prints `OK: ...` when clear to file; exits 1 with one
`REFUSED: ...` line per undisclosed high-spend retirement otherwise. A
disclosure is anything containing at least two `$x/M` figures in the ticket
body — one for the retired rung, one for its replacement — so a reviewer can
read the delta without re-deriving it from spend logs after the fact.

## Worked example (this ticket's own D7 numbers)

```
$ python3 rung_retirement_price_check.py \
    --old-config old.yaml --new-config new.yaml \
    --spend-json spend.json --ticket-body bad_ticket.txt
REFUSED: retiring mistral-small (spend mistral-small=$8.79) clears the
$5.00 review threshold but the ticket body carries fewer than 2 '$x/M'
price figures — state the retired rung's measured $/M input price and the
replacement rung's $/M input price so the delta is reviewable
(FLEET_COST_PLAN.md D7).

$ python3 rung_retirement_price_check.py \
    --old-config old.yaml --new-config new.yaml \
    --spend-json spend.json --ticket-body good_ticket.txt
OK: no undisclosed high-spend rung retirement
```

where `good_ticket.txt` states "removing mistral-small ($0.106/M) in favor
of deepseek-v4-flash ($0.208/M))" and `bad_ticket.txt` just says "removing
mistral-small, unused" — the actual wq#1593/D7 shape of the miss.
