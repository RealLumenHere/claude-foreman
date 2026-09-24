#!/usr/bin/env bash
# Install FlareSolverr (wq#2482): container unit + Gatus liveness probe.
# Idempotent: re-running converges to the same state.
#
# Not run by this BUILD worker — the sandbox's guard.sh denies docker beyond
# ps/logs and /etc is outside the worktree (same constraint documented in
# ops/wq1299-phoenix-tracer/install.sh). Run this from an interactive/root
# session on lumen.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)

sudo install -m 0644 "$HERE/flaresolverr.service" /etc/systemd/system/flaresolverr.service
sudo systemctl daemon-reload
sudo systemctl enable --now flaresolverr.service

# Redeploy the fleet-hardening Gatus config (wq#1184) with the new
# flaresolverr-liveness endpoint appended, then restart Gatus to pick it up.
# Same copy step wq1184's own install.sh uses for this file.
sudo -u lumen cp "$REPO_ROOT/ops/wq1184-gatus-health-checks/config.yaml" /home/lumen/kb/gatus/config.yaml
sudo systemctl restart lumen-gatus.service

echo "--- verify ---"
systemctl is-active flaresolverr.service
sleep 2
curl -sf -m 5 http://127.0.0.1:8191/ | head -c 200 && echo
systemctl is-active lumen-gatus.service
echo "Now: confirm off-host unreachability and run the POST /v1 challenge-solve"
echo "check from README.md's deploy checklist (not exercised by this script —"
echo "it makes an outbound request through a real Cloudflare-challenged URL)."
