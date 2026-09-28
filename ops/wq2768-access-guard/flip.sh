#!/bin/bash
# wq#2768: move chrome-mcp's Access guard from shadow to enforce, but only on evidence.
# Needs root; control-plane change, so run it while Frank is driving (right after a Chrome MCP call).
# Evidence rule: since the shadow deploy, >=1 tunnel request verified valid AND zero tunnel requests
# that would have been denied. Any tunnel deny means the pinned AUD/ISS is wrong: stay in shadow.
set -euo pipefail
F=/etc/systemd/system/chrome-mcp.service.d/30-wq2768-access-guard.conf
since=$(stat -c %y "$F" | cut -d. -f1)
log=$(journalctl -u chrome-mcp --since "$since" --no-pager -o cat | grep '"guard":"access"' | grep '"event":"verdict"' || true)
good=$(grep -c '"ok":true.*"via":"tunnel"' <<<"$log" || true)
bad=$(grep -c '"ok":false.*"via":"tunnel"' <<<"$log" || true)
echo "since=$since tunnel_valid=$good tunnel_would_deny=$bad"
if grep -q '^Environment=ACCESS_GUARD_MODE=enforce$' "$F"; then echo "already enforce"; exit 0; fi
if [ "$good" -lt 1 ] || [ "$bad" -ne 0 ]; then
  grep '"via":"tunnel"' <<<"$log" | grep '"ok":false' | tail -3 || true
  echo "NO FLIP: need >=1 valid and 0 denied tunnel requests" >&2
  exit 3
fi
cp -p "$F" "$F.bak.$(date +%Y%m%d_%H%M%S)"
sed -i 's/^Environment=ACCESS_GUARD_MODE=shadow$/Environment=ACCESS_GUARD_MODE=enforce/' "$F"
systemctl daemon-reload
systemctl restart chrome-mcp
for i in $(seq 1 40); do curl -s -o /dev/null --max-time 2 http://127.0.0.1:3103/health && break; sleep 0.5; done
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST -H 'content-type: application/json' -d '{}' http://127.0.0.1:3103/mcp)
echo "enforce: anon_mcp=$code (want 403). Now make one Chrome MCP call to confirm the tunnel path passes."
