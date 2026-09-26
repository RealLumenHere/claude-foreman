#!/usr/bin/env bash
# wq#2808 apply — NOT run by the staging session. Frank's go-ahead, then:
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq2808-tailnet-raw-lockdown/apply.sh'
#
# Adds one raw PREROUTING rule (see README.md, TEST_PLAN.md), under a detached
# auto-rollback timer: if any live check fails, or this script dies, the rule is
# deleted at T+180s and ntfy is paged. Only a clean run of every check disarms it and
# saves the ruleset. Idempotent: re-running with the rule present does nothing.
set -uo pipefail
RULE=(-i tailscale0 -p tcp -m tcp --dport 3100 -m comment --comment wq2808-commander-3100-tailnet-drop -j DROP)
BES=100.110.156.91
TS=$(date -u +%Y%m%d_%H%M%SZ)
NTFY=/home/lumen/bin/ntfy-send

if sudo -n iptables -t raw -C PREROUTING "${RULE[@]}" 2>/dev/null; then
  echo "already present; verifying persisted == live"
  diff <(sudo -n iptables-save | grep -vE '^#' | sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/') \
       <(sudo -n cat /etc/iptables/rules.v4 | grep -vE '^#' | sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/') \
    >/dev/null && { echo OK; exit 0; }
  echo "rule present but persisted file lags live -> saving"; sudo -n netfilter-persistent save; exit $?
fi

sudo -n cp /etc/iptables/rules.v4 "/etc/iptables/rules.v4.bak.${TS}_pre_wq2808" || exit 1
sudo -n iptables-save > "/home/lumen/foreman/ops/wq2808-tailnet-raw-lockdown/live_pre_apply_${TS}.v4" || exit 1

# arm rollback FIRST — a detached unit, so it survives this shell
sudo -n systemd-run --on-active=180 --unit="wq2808-rollback-${TS}" --collect \
  /bin/bash -c "iptables -t raw -D PREROUTING ${RULE[*]} && $NTFY -t lumen-alerts -T 'wq2808 ROLLED BACK' 'raw 3100 tailnet drop removed by timer (checks did not disarm)'" \
  || { echo "could not arm rollback timer; refusing to apply"; exit 1; }
echo "rollback armed: wq2808-rollback-${TS}.timer fires at T+180s unless disarmed"

sudo -n iptables -t raw -A PREROUTING "${RULE[@]}" || { echo "insert failed"; exit 1; }

fail() { echo "CHECK FAILED: $* — leaving the rollback timer armed"; exit 1; }
sudo -n iptables -t raw -C PREROUTING "${RULE[@]}" || fail "rule not present after insert"
curl -sf -m 3 -o /dev/null http://127.0.0.1:3100/health || fail "loopback 3100 (mcpw path) broke"
ssh -o BatchMode=yes -o ConnectTimeout=5 bes@$BES "curl -s -m 3 -o /dev/null http://100.117.66.102:3100/health" \
  && fail "tailnet peer STILL reaches 3100 (rule not effective)"
ssh -o BatchMode=yes -o ConnectTimeout=5 bes@$BES "curl -sf -m 3 -o /dev/null http://100.117.66.102:4000/health/liveliness" \
  || fail "LiteLLM 4000 over tailnet broke"
ssh -o BatchMode=yes -o ConnectTimeout=5 bes@$BES "nc -z -w3 100.117.66.102 22" || fail "SSH 22 over tailnet not open from Bes"

sudo -n systemctl stop "wq2808-rollback-${TS}.timer" 2>/dev/null; sudo -n systemctl stop "wq2808-rollback-${TS}.service" 2>/dev/null
sudo -n netfilter-persistent save || fail "persist failed (rule live, timer disarmed; run netfilter-persistent save)"
diff <(sudo -n iptables-save | grep -vE '^#' | sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/') \
     <(sudo -n cat /etc/iptables/rules.v4 | grep -vE '^#' | sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/') >/dev/null \
  || fail "persisted != live after save"
$NTFY -t lumen-fleet -T 'wq2808 applied' 'raw PREROUTING 3100 tailnet drop live + persisted; all checks passed' >/dev/null 2>&1
echo "APPLIED + PERSISTED + VERIFIED. backup: /etc/iptables/rules.v4.bak.${TS}_pre_wq2808"
echo "next: python3 /home/lumen/minerva/lumen_episode.py commit --summary 'wq#2808 applied raw 3100 tailnet drop' --kind ops-apply --ref wq#2808"
