#!/usr/bin/env bash
# wq#4181 — tf1-c: tailnet-only ADB host role config.
# Run after harden-armbian.sh. Idempotent.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "tf1-c-adb-host.sh must run as root" >&2
  exit 1
fi

TAILSCALE_IFACE=tailscale0

# adb server bound to all interfaces (-a) so tailnet peers can reach it
# in-process; -P 5037 is the standard port; nodaemon keeps it foreground so
# systemd can supervise it (adb's default server mode double-forks and exits,
# which systemd would read as a crash).
install -m 0644 /dev/stdin /etc/systemd/system/adb-server.service <<'UNIT'
[Unit]
Description=ADB server (tailnet-only ADB host, tf1-c)
After=network-online.target tailscaled.service tf1-c-adb-firewall.service
Wants=network-online.target
Requires=tf1-c-adb-firewall.service

[Service]
Type=simple
ExecStart=/usr/bin/adb -a -P 5037 nodaemon server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# Firewall: only accept :5037 from the tailscale interface; drop it from
# every other interface (LAN, WAN) so "-a" doesn't become a LAN-wide exposure.
install -m 0755 /dev/stdin /usr/local/sbin/tf1-c-adb-firewall.sh <<FW
#!/usr/bin/env bash
set -euo pipefail
iptables -C INPUT -i ${TAILSCALE_IFACE} -p tcp --dport 5037 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -i ${TAILSCALE_IFACE} -p tcp --dport 5037 -j ACCEPT
iptables -C INPUT -p tcp --dport 5037 -j DROP 2>/dev/null \
  || iptables -A INPUT -p tcp --dport 5037 -j DROP
FW

install -m 0644 /dev/stdin /etc/systemd/system/tf1-c-adb-firewall.service <<'UNIT'
[Unit]
Description=Lock ADB server (:5037) to the tailscale interface
After=network-online.target
Wants=network-online.target
Before=adb-server.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tf1-c-adb-firewall.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

systemctl enable tf1-c-adb-firewall.service adb-server.service

echo "tf1-c-adb-host.sh: adb-server.service + tailnet-only iptables firewall installed."
