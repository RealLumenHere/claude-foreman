#!/usr/bin/env bash
# Rewrite ansible_host lines in inventory/hosts.yml from lumen_state.py,
# the authoritative node→IP source (tailnet IPs rotate; instinct: never
# assume an IP hasn't changed). Non-destructive: unknown nodes keep their
# committed value; a .bak is left beside the inventory.
set -euo pipefail
cd "$(dirname "$0")/.."
INV=ansible/inventory/hosts.yml
STATE=${LUMEN_STATE:-/home/lumen/bin/lumen_state.py}

# NOTE: /home/lumen/bin/lumen_state.py is a shell shim (execs the real
# script in minerva) — invoke it directly, never as `python3 <shim>`.
[ -x "$STATE" ] || { echo "lumen_state.py not found/executable at $STATE"; exit 1; }

python3 - "$INV" "$STATE" <<'PYEOF'
import re, subprocess, sys
inv_path, state = sys.argv[1], sys.argv[2]
text = open(inv_path).read()
open(inv_path + ".bak", "w").write(text)
updated = []
# every host key that appears directly under a hosts: block
nodes = re.findall(r"^        (\w+):", text, re.M)
for node in nodes:
    try:
        out = subprocess.run(
            [state, "current", node],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        continue
    # fact line format: `  <node>.tailscale_ip   = 100.x.y.z  (since ...)`
    m = re.search(rf"{node}\.tailscale_ip\s*=\s*(100\.\d+\.\d+\.\d+)", out)
    if not m:
        continue
    ip = m.group(1)
    updated.append(f"{node}={ip}")
    pat = rf"(^        {node}:\n          ansible_host: )\S+"
    if re.search(pat, text, re.M):
        text = re.sub(pat, rf"\g<1>{ip}", text, flags=re.M)
    else:
        text = re.sub(
            rf"(^        {node}:\n)", rf"\1          ansible_host: {ip}\n", text, flags=re.M
        )
open(inv_path, "w").write(text)
if not updated:
    sys.exit("no node resolved a tailscale_ip from lumen_state — refusing to claim success")
print("inventory refreshed from", state, "—", ", ".join(updated))
PYEOF
