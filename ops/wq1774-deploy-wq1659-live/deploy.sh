#!/usr/bin/env bash
# wq#1774: deploy wq#1659's dangling-After= fix (port corrected to 5433, per
# wq#1765) live, folding in wq#1771's 2 extra units (lumen-brain-health,
# lumen-predicate-audit) so unit-drift check goes clean in one pass.
#
# Root-only (writes under /etc/systemd/system and re-baselines the
# root-readable git repo at /home/lumen/fleet-units). Run via:
#   /home/lumen/bin/lumen-run start --root -- \
#     bash /home/lumen/worktrees/wq1774-a2/ops/wq1774-deploy-wq1659-live/deploy.sh
#
# Idempotent: `install -m` no-ops when content matches; the unit edits below
# are guarded by `grep -q` so a re-run does not double-apply a hunk that a
# prior (possibly killed) run of this same script already landed.
set -euo pipefail

SRC_1659=/home/lumen/worktrees/wq1774-a2/ops/wq1659-dangling-service-afters
SRC_1771=/home/lumen/worktrees/wq1774-a2/ops/wq1771-dangling-litellm-after

install -m 0755 "$SRC_1659/wait-for-port" /home/lumen/bin/wait-for-port
install -m 0755 "$SRC_1659/staged/unit-drift" /home/lumen/bin/unit-drift

# 5 units from wq#1659 (units.diff): drop dangling After=/Wants=litellm.service
# or After=lumen-pgvector.service, gate on docker.service + a port wait instead.
for u in lumen-fleet-cerebras lumen-fleet-mistral lumen-fleet-openrouter; do
  f="/etc/systemd/system/${u}.service"
  if grep -q '^After=litellm.service$' "$f" 2>/dev/null; then
    sed -i \
      -e '/^After=litellm\.service$/d' \
      -e '/^Wants=litellm\.service$/d' \
      -e '/^Requires=docker\.service$/i After=docker.service' \
      "$f"
    sed -i '/^\[Service\]/a ExecStartPre=/home/lumen/bin/wait-for-port 127.0.0.1 4000 30' "$f"
  fi
done

f=/etc/systemd/system/lumen-convo-learn.service
if grep -q '^After=litellm.service$' "$f" 2>/dev/null; then
  sed -i \
    -e 's/^After=litellm\.service$/After=docker.service/' \
    -e '/^Wants=litellm\.service$/d' \
    "$f"
  grep -q '^Requires=docker.service$' "$f" || sed -i '/^After=docker\.service$/a Requires=docker.service' "$f"
  grep -q 'ExecStartPre=/home/lumen/bin/wait-for-port' "$f" || \
    sed -i '/^\[Service\]/a ExecStartPre=/home/lumen/bin/wait-for-port 127.0.0.1 4000 30' "$f"
fi

f=/etc/systemd/system/lumen-research-index.service
if grep -q '^After=lumen-pgvector.service$' "$f" 2>/dev/null; then
  sed -i 's/^After=lumen-pgvector\.service$/After=docker.service/' "$f"
  grep -q '^Requires=docker.service$' "$f" || sed -i '/^After=docker\.service$/a Requires=docker.service' "$f"
  grep -q 'ExecStartPre=/home/lumen/bin/wait-for-port' "$f" || \
    sed -i '/^\[Service\]/a ExecStartPre=/home/lumen/bin/wait-for-port 127.0.0.1 5433 30' "$f"
fi

# 2 units from wq#1771: no docker gate needed, just drop the dangling token.
f=/etc/systemd/system/lumen-brain-health.service
sed -i 's/^After=network.target litellm.service$/After=network.target/' "$f"
f=/etc/systemd/system/lumen-predicate-audit.service
sed -i 's/^After=network-online.target litellm.service$/After=network-online.target/' "$f"

systemctl daemon-reload

for u in lumen-fleet-cerebras lumen-fleet-mistral lumen-fleet-openrouter \
         lumen-convo-learn lumen-research-index lumen-brain-health lumen-predicate-audit; do
  systemd-analyze verify "/etc/systemd/system/${u}.service"
done

for u in lumen-fleet-cerebras lumen-fleet-mistral lumen-fleet-openrouter; do
  systemctl is-active --quiet "$u" || { echo "wq1774: $u not active after reload" >&2; exit 1; }
done

echo "wq1774: unit edits applied, daemon-reloaded, verified, fleet units active"
echo "wq1774: run 'python3 /home/lumen/bin/unit-drift snapshot && python3 /home/lumen/bin/unit-drift check' as the lumen user (not root) next --"
echo "wq1774: /home/lumen/fleet-units is lumen-owned, and git flags a root euid against it as dubious ownership"
