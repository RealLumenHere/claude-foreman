#!/usr/bin/env bash
# provision-fworker.sh — privileged M80s provisioning for wq#3652 steps 3b/4/5.
#
# Run as root on the M80 node AFTER Ubuntu is installed on nvme0n1 (step 3a is
# physical/console-only — see README.md in this directory). This script itself
# needs useradd, mkfs.ext4 and mount, none of which a fworker BUILD-worker Bash
# sandbox may run: harness/worker-settings.json's allowlist has no useradd/mkfs/
# mount entry, and harness/fleet-exec-policy.yaml's never.sudo_argv_deny
# permanently excludes useradd and "mkfs*" even once fleet-exec grows a sudo
# verb (never.* always wins over any grant). It is authored here for a
# privileged/root session or Frank to run — this wq#3652 BUILD worker does not
# and cannot execute it.
#
# Not validated against real M80 hardware: no M80 node is network-reachable yet
# (checked lumen_state.py and harness/fleet-exec-policy.yaml — neither tracks
# one), so this is a reviewed-but-unexecuted reference script, not demonstrated
# evidence. Re-read it against the real device names (lsblk) before running.
set -euo pipefail

NVME_DATA_DEV="${NVME_DATA_DEV:-/dev/nvme1n1}"
FWORKER_HOME=/var/fworker
UNIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "provision-fworker.sh: must run as root (privileged/console session, never a BUILD worker)" >&2
  exit 1
fi

if ! id fworker >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$FWORKER_HOME" --shell /usr/sbin/nologin fworker
fi

if ! blkid "$NVME_DATA_DEV" >/dev/null 2>&1; then
  mkfs.ext4 -L fworker-data "$NVME_DATA_DEV"
fi

mkdir -p "$FWORKER_HOME"
grep -q "^${NVME_DATA_DEV} " /etc/fstab 2>/dev/null || \
  echo "${NVME_DATA_DEV} ${FWORKER_HOME} ext4 defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$FWORKER_HOME" || mount "$FWORKER_HOME"

mkdir -p "$FWORKER_HOME"/.locks "$FWORKER_HOME"/bin "$FWORKER_HOME"/.secrets \
         "$FWORKER_HOME"/foreman/ops/wq3652-m80s-provisioning
chown -R fworker:fworker "$FWORKER_HOME"
chmod 0750 "$FWORKER_HOME"
chmod 0700 "$FWORKER_HOME"/.secrets

# fworker's own foreman checkout belongs here (git clone as the fworker user, or
# an rsync from Lumen's /home/lumen/foreman) — out of scope for this script,
# which only installs the sandbox unit and its supporting files.
install -m 0755 -o fworker -g fworker \
  "$UNIT_DIR/fworker_sandbox_slot.sh" \
  "$FWORKER_HOME/foreman/ops/wq3652-m80s-provisioning/fworker_sandbox_slot.sh"
install -m 0755 -o fworker -g fworker "$UNIT_DIR/wq-remote" "$FWORKER_HOME/bin/wq-remote"
if [[ ! -e "$FWORKER_HOME/fworker.env" ]]; then
  install -m 0600 -o fworker -g fworker "$UNIT_DIR/fworker.env.example" "$FWORKER_HOME/fworker.env"
fi
install -m 0644 "$UNIT_DIR/fworker-sandbox@.service" /etc/systemd/system/fworker-sandbox@.service

systemctl daemon-reload
echo "provision-fworker.sh: done. Start a slot with:"
echo "  install -m0644 /dev/stdin /var/fworker/.locks/fworker-sandbox-1.env <<<'TICKET_ID=<n>\nREPO=<path>'"
echo "  systemctl start fworker-sandbox@1"
