#!/usr/bin/env bash
# wq#4319 — tf1-d: Limpet headless testbed role config.
# Installs systemd units for the two LIMPET daemons this role is meant to
# run unattended: the HAL daemon (ops/wq3809-limpet-hal/hal_daemon.py,
# which explicitly wants Type=simple/--foreground under systemd) and the
# plugin registry daemon (limpet/daemon.py, which the module docstring
# says is "meant to run detached (systemd unit / lumen-run), not
# foreground-unattended per fleet instinct 5" — i.e. exactly this unit).
#
# Does NOT touch the MCP meta-server (ops/wq3808-limpet-mcp-meta-server-a1/
# meta_server.py): it ships with every manifest.example.json entry disabled
# under the wq#3786 REA SECURITY HOLD (TOOL_REGISTRY.md), and nothing in
# this ticket is a Frank-reviewed change to that status. render_role.py's
# validate_role() rejects a roles.yaml edit that flips meta_server_enabled
# to true, so that hold can't regress silently via this role either.
#
# Assumes /opt/foreman already holds a checkout of this repo (bench task,
# see roles.yaml: physical_tasks — no fleet-exec grant to do that sync from
# this worktree). Run after harden-armbian.sh. Idempotent.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "tf1-d-limpet-testbed.sh must run as root" >&2
  exit 1
fi

FOREMAN_CHECKOUT=/opt/foreman

install -m 0644 /dev/stdin /etc/systemd/system/limpet-hal-daemon.service <<UNIT
[Unit]
Description=LIMPET Hardware Abstraction Layer daemon (tf1-d testbed)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${FOREMAN_CHECKOUT}/ops/wq3809-limpet-hal
ExecStart=/usr/bin/python3 ${FOREMAN_CHECKOUT}/ops/wq3809-limpet-hal/hal_daemon.py run --foreground
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

install -m 0644 /dev/stdin /etc/systemd/system/limpet-registryd.service <<UNIT
[Unit]
Description=LIMPET plugin registry reconcile daemon (tf1-d testbed)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${FOREMAN_CHECKOUT}/limpet
ExecStart=/usr/bin/python3 ${FOREMAN_CHECKOUT}/limpet/daemon.py --interval 30
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl enable limpet-hal-daemon.service limpet-registryd.service

echo "tf1-d-limpet-testbed.sh: limpet-hal-daemon.service + limpet-registryd.service installed (MCP meta-server left disabled, wq#3786 hold)."
