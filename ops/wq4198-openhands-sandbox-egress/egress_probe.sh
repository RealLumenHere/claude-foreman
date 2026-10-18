#!/usr/bin/env bash
# wq#4198 reach probe. Starts a throwaway container on docker0 NAMED like a
# sandbox (oh-agent-server-egressprobe-*), so lumen-oh-sandbox-guard treats it
# exactly as it treats a real OpenHands sandbox, then tries TCP to each target.
#   egress_probe.sh <image> before   # baseline: expects every target OPEN
#   egress_probe.sh <image> after    # expects only the two allowlisted OPEN
# <image> must already be local and have bash (the agent-server image does:
# docker images | grep agent-server). Exit 0 = matrix matched, 1 = mismatch.
set -uo pipefail
IMAGE="${1:?image}"; MODE="${2:?before|after}"
ALLOWED="172.17.0.1:4000 172.17.0.1:3000"
# The ticket's measured-open set (host sshd x2, Horus LAN, Ma'at LAN, internet).
BLOCKED="172.17.0.1:22 192.168.8.160:22 192.168.8.231:22 192.168.8.202:22 1.1.1.1:443"
# Not in the ticket's measurement (Tailscale ACLs may close it anyway), so
# checked CLOSED after and ignored before: Horus tailnet sshd.
AFTER_ONLY="100.83.19.124:22"
NAME="oh-agent-server-egressprobe-$$"

script='for t in "$@"; do h=${t%:*}; p=${t#*:}; if timeout 4 bash -c "</dev/tcp/$h/$p" 2>/dev/null; then echo "$t OPEN"; else echo "$t CLOSED"; fi; done'
# The guard reconciles on the start event; give it a beat so this probe sees
# the same state a sandbox would after its (>=15s) agent-server boot.
out="$(docker run --rm --name "$NAME" --network bridge --entrypoint bash "$IMAGE" -c "sleep 3; $script" _ $ALLOWED $BLOCKED $AFTER_ONLY)"
echo "$out"

fail=0
for t in $ALLOWED; do grep -qx "$t OPEN" <<<"$out" || { echo "MISMATCH $t should be OPEN"; fail=1; }; done
for t in $BLOCKED; do
  want=CLOSED; [ "$MODE" = before ] && want=OPEN
  grep -qx "$t $want" <<<"$out" || { echo "MISMATCH $t should be $want"; fail=1; }
done
if [ "$MODE" = after ]; then
  for t in $AFTER_ONLY; do grep -qx "$t CLOSED" <<<"$out" || { echo "MISMATCH $t should be CLOSED"; fail=1; }; done
fi
[ $fail = 0 ] && echo "PROBE $MODE: matrix matched" || echo "PROBE $MODE: MISMATCH"
exit $fail
