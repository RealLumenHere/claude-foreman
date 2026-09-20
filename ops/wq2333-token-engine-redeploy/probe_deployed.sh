#!/usr/bin/env bash
# wq#2333 — deploy-drift probe for the token-engine broker: is the copy at
# /usr/local/lib (the one lumen-pr rides) at or behind the checkout? Prints
# booleans, sizes and sha256 only, never a rule body, so the probe output can
# itself be quoted in a PR without arming a mask.
#
#   /home/lumen/bin/lumen-run start --root -- bash <this> [out-file]
set -uo pipefail

DEPLOYED=/usr/local/lib/token-engine/token_engine_daemon.py
REPO=/home/lumen/foreman/token-engine/token_engine_daemon.py
OUT="${1:-/tmp/wq2333-te-probe.txt}"

{
    echo "=== wq#2333 token-engine deploy probe $(date -Is) ==="
    for f in "$DEPLOYED" "$REPO"; do
        if [ ! -f "$f" ]; then
            echo "$f: ABSENT"
            continue
        fi
        echo "--- $f"
        echo "  mtime : $(stat -c %y "$f")"
        echo "  bytes : $(stat -c %s "$f")"
        echo "  sha256: $(sha256sum "$f" | cut -d' ' -f1)"
        echo "  boundary_anchor_lines: $(grep -c '(?<!\[A-Za-z0-9\])' "$f")"
        echo "  wq2292_markers: $(grep -c 'wq#2292' "$f")"
        echo "  wq2298_markers: $(grep -c 'wq#2298' "$f")"
    done
    echo "--- service"
    systemctl is-active token-engine.service
    systemctl show token-engine.service -p ActiveEnterTimestamp --value
    ls -l /run/token-engine/broker.sock 2>&1
} > "$OUT" 2>&1
chmod 0644 "$OUT"
echo "probe written to $OUT"
