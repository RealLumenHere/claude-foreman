#!/usr/bin/env bash
# wq#2608 -- DNS CNAME + CF Access app for mcp-bes.lumennode.uk.
# Two DIFFERENT credentials are involved here, not one -- worth separating
# because one of them may already be usable without wq#2614:
#
#   1. DNS route (`cloudflared tunnel route dns`) authenticates with the
#      ORIGIN CERT the tunnel was created with (~/.cloudflared/cert.pem for
#      the bes user) -- the same credential that already lets Bes's existing
#      tunnel serve its other hostnames. If that cert has Zone:DNS:Edit for
#      lumennode.uk (it must, or the existing tunnel's other CNAMEs couldn't
#      exist), this step does NOT need wq#2614's Workers-scoped API token.
#      Verify the cert grants this before assuming -- don't guess.
#   2. Creating a CF Access application is a dashboard/API-only operation
#      (no `cloudflared access create-app` subcommand exists) -- it genuinely
#      needs a CF API token with Access:Apps and Policies:Edit, which is
#      exactly wq#2614's still-outstanding physical task (frank_gated,
#      /home/lumen/.secrets/cf_token does not exist as of this ticket).
#
# Run ON BES as the bes user (step 1) / from anywhere with the token (step 2).
set -euo pipefail

HOSTNAME=mcp-bes.lumennode.uk
CFD_CONFIG=/etc/cloudflared/config.yml
ACCESS_POLICY_ID=e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0   # existing 'Worker service token' policy, reused (design §3.2/§6)
CF_ACCOUNT_ID=3f413ce08c3163f99984a78720fa8b97           # from wq#2614's ticket text

echo "--- step 1: DNS route (needs Bes's cloudflared origin cert, NOT necessarily wq#2614) ---"
if [ -f "$CFD_CONFIG" ]; then
  # Read the tunnel identifier live rather than guessing it (doctrine: never
  # guess a config value -- verify it).
  TUNNEL=$(python3 -c "import yaml,sys; print(yaml.safe_load(open('$CFD_CONFIG'))['tunnel'])")
  echo "tunnel (from $CFD_CONFIG): $TUNNEL"
  echo "run: cloudflared tunnel route dns \"$TUNNEL\" $HOSTNAME"
else
  echo "$CFD_CONFIG not found on this box -- run step 1 ON BES."
fi

echo
echo "--- step 2: CF Access app (needs wq#2614's /home/lumen/.secrets/cf_token) ---"
if [ ! -f /home/lumen/.secrets/cf_token ]; then
  echo "BLOCKED: /home/lumen/.secrets/cf_token does not exist (wq#2614, frank_gated,"
  echo "a physical act on Frank's own CF account -- not something any session can do"
  echo "for itself). Once it exists, run:"
  cat <<'EOF'

  CF_API_TOKEN=$(cat /home/lumen/.secrets/cf_token)
  curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/apps" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data @- <<JSON
{
  "name": "mcp-bes",
  "domain": "${HOSTNAME}",
  "type": "self_hosted",
  "session_duration": "24h",
  "policies": [{"id": "${ACCESS_POLICY_ID}", "precedence": 1}]
}
JSON

EOF
  echo "(pattern of the existing mcp-failover Access app, wq#06-29; verify the exact"
  echo "policies[] shape against the live mcp-failover app's config before running --"
  echo "cite it, don't guess it, e.g. via 'GET /accounts/{id}/access/apps' filtered to"
  echo "that app.)"
  exit 0
fi

echo "cf_token present -- see the payload above; this script does not execute the"
echo "curl itself (no token is available in this sandbox to test the shape against)."

echo
echo "--- verification (run after both steps land) ---"
cat <<EOF
curl -sI https://${HOSTNAME}/mcp                     # expect 403 (no Access token)
curl -s https://${HOSTNAME}/mcp \\
  -H "CF-Access-Client-Id: <service-token-id>" \\
  -H "CF-Access-Client-Secret: <service-token-secret>" \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'  # expect 200 + a "result" key
EOF
