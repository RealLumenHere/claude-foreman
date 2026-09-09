#!/usr/bin/env bash
# wq#1573 — dedicated glm-5.3-flash line for /home/lumen/bin/litellm-cost.
#
# litellm-cost's existing "BY MODEL" section already includes glm-5.3-flash
# once it has any spend (it groups all provided_model_name values), but that
# section is cost-sorted and LIMIT 20 — a cheap, low-volume rung can still
# scroll off or get lost next to noisier models. This gives glm-5.3-flash its
# own line unconditionally, so a promo-pricing change or a routing regression
# shows up even at near-zero call volume. Not a new logging path — same
# read-only Langfuse clickhouse source litellm-cost already uses.
#
# Deploy: append this section to /home/lumen/bin/litellm-cost (same $D window
# variable, same `q()` helper) — a manual one-line copy-paste since
# litellm-cost lives outside this repo's git history.
set -euo pipefail
D="${1:-7}"
CH="docker exec langfuse-clickhouse clickhouse-client"
q(){ $CH -q "$1" 2>/dev/null; }

echo; echo "### GLM-5.3-FLASH (wq#1573 watch rung, added 2026-09-09)"
q "SELECT count(*) AS calls, round(sum(total_cost),4) AS cost_usd,
   sum(usage_details['input']) AS in_tok, sum(usage_details['output']) AS out_tok,
   round(sum(total_cost)/greatest(count(*),1),6) AS usd_per_call
   FROM observations
   WHERE start_time > now() - toIntervalDay(${D})
     AND provided_model_name LIKE '%glm-5.3-flash%'
   FORMAT PrettyCompact"
