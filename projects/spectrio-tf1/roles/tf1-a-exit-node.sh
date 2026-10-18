#!/usr/bin/env bash
# wq#4181 — tf1-a: tailscale exit node + subnet router role config.
# Run after harden-armbian.sh. Idempotent.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "tf1-a-exit-node.sh must run as root" >&2
  exit 1
fi

# ip_forward: required for tailscale to relay traffic as an exit node /
# subnet router (tailscale's own documented prerequisite).
install -m 0644 /dev/stdin /etc/sysctl.d/99-tailscale.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl -p /etc/sysctl.d/99-tailscale.conf

# UDP GRO forwarding: tailscale's documented throughput tuning for an exit
# node / subnet router. ethtool settings do not survive a link down/up, so a
# networkd-dispatcher hook re-applies them every time an interface goes
# routable (networkd-dispatcher exports IFACE to each hook it runs).
mkdir -p /etc/networkd-dispatcher/routable.d
install -m 0755 /dev/stdin /etc/networkd-dispatcher/routable.d/50-tailscale-gro <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
case "$IFACE" in
  lo|tailscale*) exit 0 ;;
esac
ethtool -K "$IFACE" rx-udp-gro-forwarding on rx-gro-list off 2>/dev/null || true
HOOK

# Performance governor: exit-node traffic is latency/throughput sensitive —
# don't let cpufreq step the CPU down under load.
install -m 0644 /dev/stdin /etc/systemd/system/cpufreq-performance.service <<'UNIT'
[Unit]
Description=Pin cpufreq governor to performance (tf1-a exit node)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$g"; done'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable cpufreq-performance.service

echo "tf1-a-exit-node.sh: sysctl ip_forward, GRO hook, performance governor installed."
echo "Run once, interactively, after tailscaled is up and authed:"
echo "  tailscale up --advertise-exit-node --advertise-routes=192.168.8.0/24"
echo "Then approve the exit node + route in the tailnet admin console."
