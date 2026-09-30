#!/usr/bin/env bash
# Deploys lumen-mail-probe and refuses to finish quietly if the deploy drops
# the cron trigger. wq#3209: on 2026-09-07 a deploy shipped with
# workers/scripts/lumen-mail-probe/schedules = [] — no cron ever fired the
# probe, so guardian check E2-roundtrip sat UNCONFIGURED for 23 days before
# anyone noticed. A deploy that does not end by reading the trigger back from
# Cloudflare cannot know that happened.
set -euo pipefail
cd "$(dirname "$0")"

TOKEN_FILE="${CF_DEPLOY_TOKEN_FILE:-/home/lumen/mailbot/.cf_deploy_token}"
ACCOUNT_FILE="${CF_ACCOUNT_ID_FILE:-/home/lumen/mailbot/.cf_account_id}"
WORKER_NAME="${PROBE_WORKER_NAME:-lumen-mail-probe}"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  if [ ! -f "$TOKEN_FILE" ]; then
    echo "DEPLOY REFUSED: CLOUDFLARE_API_TOKEN is unset and $TOKEN_FILE does not exist." >&2
    exit 1
  fi
  CLOUDFLARE_API_TOKEN=$(< "$TOKEN_FILE")
  export CLOUDFLARE_API_TOKEN
fi
if [ ! -f "$ACCOUNT_FILE" ]; then
  echo "DEPLOY REFUSED: $ACCOUNT_FILE does not exist." >&2
  exit 1
fi
ACCOUNT_ID=$(< "$ACCOUNT_FILE")

npx wrangler deploy

echo "== reading back registered cron schedules =="
SCHEDULES=$(curl -sS \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$WORKER_NAME/schedules")

python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
if not doc.get("success"):
    print(f"DEPLOY FAILED SCHEDULES READ-BACK: {doc.get(\"errors\")}", file=sys.stderr)
    sys.exit(1)
schedules = (doc.get("result") or {}).get("schedules") or []
if not schedules:
    print("DEPLOY SUCCEEDED BUT CRON TRIGGER IS NOT REGISTERED "
          "(schedules == []) -- wrangler.toml [triggers] did not take. "
          "guardian E2-roundtrip will read this as UNCONFIGURED until it is "
          "fixed (wq#3209). Re-run wrangler deploy or check wrangler.toml.",
          file=sys.stderr)
    sys.exit(1)
print(f"OK: {len(schedules)} cron schedule(s) registered: "
      f"{[s.get(\"cron\") for s in schedules]}")
' "$SCHEDULES"
