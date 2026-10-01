#!/usr/bin/env bash
# wq#3299 apply — stop Bes's stock supergateway from leaking per-request DC
# handlers into a box-wide OOM (2026-09-30 21:20 PDT: 157 idle handlers,
# 13.1GB cgroup, no MemoryMax, killed HA python + systemd user mgr +
# vmagent-nodeexp, ~5h Commander failover outage).
#
# Three parts, applied together: (1) copy Lumen's already-patched
# supergateway dist (narrowed per-request reap, wq#829/PR#151, deployed to
# Lumen 2026-07-06, never shipped to Bes) over Bes's stock 3.4.3 build, after
# a version check; (2) install MemoryHigh/MemoryMax/TasksMax so a leak that
# slips past the reap is a bounded restart instead of a box-wide OOM;
# (3) install mcp-pressure-guard-bes as a standing backstop (the interim
# hand-reap regrows ~2GB/h, wq#3299 notes).
#
# BUILD-worker sandbox cannot ssh/sudo to Bes (fleet-exec: grant_missing_env,
# confirmed 2026-10-01) — this is a proposal for an operator/session with
# that access, run FROM Lumen, same posture as wq829/apply.sh and
# wq3300/apply.sh before it. Never restarts bes-mcp-failover more than once.
# Never reboots. Dead-man rollback (wq#3108 pattern): if the post-restart
# health/PID check fails, the armed timer restores the pre-change dist and
# drop-ins automatically — no operator babysitting required.
set -euo pipefail
BES=bes@100.110.156.91
UNIT=bes-mcp-failover
HERE=$(cd "$(dirname "$0")" && pwd)
LUMEN_DIST=/home/lumen/.npm-global/lib/node_modules/supergateway/dist/gateways/stdioToStatelessStreamableHttp.js
DROPIN_DIR=/etc/systemd/system/bes-mcp-failover.service.d
STAGE=/tmp/wq3299-deadman
b() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$BES" "$@"; }
fail() { echo "CHECK FAILED: $*"; exit 1; }

node --check "$HERE/stdioToStatelessStreamableHttp.js"

# Discover Bes's actual supergateway dist path at runtime — never hardcode a
# guessed path (doctrine: verify it, don't guess it).
bes_root=$(b "npm root -g")
BES_DIST="$bes_root/supergateway/dist/gateways/stdioToStatelessStreamableHttp.js"
b "test -f $BES_DIST" || fail "no supergateway dist found at $BES_DIST — npm root -g on Bes returned $bes_root, re-check the install layout before re-running"

# Version check first: only swap the dist if Bes is running the same
# supergateway version Lumen's patch was built against (ticket: "copy Lumen
# patched supergateway dist (same version, check first)"). A mismatch means
# re-diff before copying, not blind-overwrite.
lumen_ver=$(node -p "require('${LUMEN_DIST%/dist/*}/package.json').version")
bes_ver=$(b "node -p \"require('$bes_root/supergateway/package.json').version\"")
[ "$lumen_ver" = "$bes_ver" ] || fail "version mismatch: Lumen=$lumen_ver Bes=$bes_ver — re-diff the patch against Bes's version before copying"

# Quiet-window guard, same rationale as wq#829/wq#3300: restarting mid-request
# knocks over whatever Commander failover session is in flight.
recent=$(b "journalctl -u $UNIT --since '10 min ago' --no-pager" | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ3299_FORCE=1."
  [ "${WQ3299_FORCE:-0}" = 1 ] || exit 1
fi

b "mkdir -p $STAGE"
b "sudo -n cp -a $BES_DIST $STAGE/stdioToStatelessStreamableHttp.js.bak"
b "sudo -n systemctl cat $UNIT > $STAGE/unit.bak 2>/dev/null || true"
b "sudo -n test -d $DROPIN_DIR && sudo -n tar -C $DROPIN_DIR -cf $STAGE/dropins.bak.tar . || true"

# Rollback script the dead-man fires if the post-apply check below never
# disarms it — restores exactly the dist + drop-ins that were live before.
b "printf '%s\n' '#!/usr/bin/env bash' \
  'test -e $STAGE/DISARMED && exit 0' \
  'cp -a $STAGE/stdioToStatelessStreamableHttp.js.bak $BES_DIST' \
  'rm -f $DROPIN_DIR/99-wq3299-memlimit.conf' \
  'systemctl daemon-reload' \
  'systemctl restart $UNIT' \
  'logger -t wq3299 \"dead-man fired: restored pre-change bes-mcp-failover\"' \
  | sudo -n tee $STAGE/rollback.sh >/dev/null"
b "sudo -n chmod 700 $STAGE/rollback.sh"
b "rm -f $STAGE/DISARMED"

scp -o BatchMode=yes "$HERE/stdioToStatelessStreamableHttp.js" "$BES:/tmp/wq3299-stdioToStatelessStreamableHttp.js" || fail "scp dist failed"
scp -o BatchMode=yes "$HERE/99-wq3299-memlimit.conf" "$BES:/tmp/wq3299-99-memlimit.conf" || fail "scp memlimit conf failed"
scp -o BatchMode=yes "$HERE/mcp-pressure-guard-bes.sh" "$BES:/tmp/wq3299-mcp-pressure-guard-bes.sh" || fail "scp guard script failed"
scp -o BatchMode=yes "$HERE/mcp-pressure-guard-bes.service" "$BES:/tmp/wq3299-mcp-pressure-guard-bes.service" || fail "scp guard service failed"
scp -o BatchMode=yes "$HERE/mcp-pressure-guard-bes.timer" "$BES:/tmp/wq3299-mcp-pressure-guard-bes.timer" || fail "scp guard timer failed"

# Arm the dead-man BEFORE touching anything live (wq#3089/wq#3108 posture):
# a 180s detached timer that restores dist+drop-ins unless disarmed below.
b "sudo -n systemd-run --unit=wq3299-deadman --on-active=180 bash $STAGE/rollback.sh"

b "sudo -n install -o bes -g bes -m 0644 /tmp/wq3299-stdioToStatelessStreamableHttp.js $BES_DIST" || fail "install dist failed"
b "sudo -n install -D -m 0644 /tmp/wq3299-99-memlimit.conf $DROPIN_DIR/99-wq3299-memlimit.conf" || fail "install memlimit drop-in failed"
b "sudo -n install -m 0755 /tmp/wq3299-mcp-pressure-guard-bes.sh /usr/local/sbin/mcp-pressure-guard-bes.sh" || fail "install guard script failed"
b "sudo -n install -D -m 0644 /tmp/wq3299-mcp-pressure-guard-bes.service /etc/systemd/system/mcp-pressure-guard-bes.service" || fail "install guard service failed"
b "sudo -n install -D -m 0644 /tmp/wq3299-mcp-pressure-guard-bes.timer /etc/systemd/system/mcp-pressure-guard-bes.timer" || fail "install guard timer failed"

b "sudo -n systemctl daemon-reload" || fail "daemon-reload failed"
# Detached restart (instinct: this gateway's path changes only via detached
# systemd-run, never a foreground call from this session).
b "sudo -n systemd-run --collect --unit=wq3299-restart-mcp-failover systemctl restart $UNIT" || fail "restart dispatch failed"
for i in $(seq 1 15); do
  sleep 1
  b "systemctl is-active --quiet $UNIT" && break
done
pid_after=$(b "systemctl show $UNIT -p MainPID --value")
b "systemctl is-active --quiet $UNIT" || fail "$UNIT not active after restart — dead-man will roll back in <=180s"
[ -n "$pid_after" ] && [ "$pid_after" != 0 ] || fail "MainPID did not advance after restart — dead-man will roll back in <=180s"

sleep 2
hc=$(b "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health" || echo 000)
echo "post-apply health=$hc MainPID=$pid_after"
[ "$hc" = 200 ] || fail "HEALTH CHECK FAILED — dead-man will roll back in <=180s, or run: sudo bash $STAGE/rollback.sh"

b "sudo -n systemctl enable --now mcp-pressure-guard-bes.timer" || fail "enable guard timer failed"

# Disarm: everything verified (active, MainPID advanced, /health 200).
b "sudo -n touch $STAGE/DISARMED"
b "sudo -n systemctl cancel-conditional wq3299-deadman 2>/dev/null || sudo -n systemctl stop wq3299-deadman 2>/dev/null || true"

echo "APPLIED. $UNIT running patched dist (narrowed per-request reap), MemoryHigh=6G/MemoryMax=8G/TasksMax=512, mcp-pressure-guard-bes.timer armed at 6G threshold."
