#!/usr/bin/env bash
# wq#857 — install VictoriaLogs on Bes (single binary, alongside the existing
# VictoriaMetrics on the same box). Root required (system user, /usr/local/bin,
# /etc/systemd/system). Run this ON Bes as root — this repo's convention is
# via lumen-run --root on the target host itself, or Frank/claude-web over SSH
# (see README.md in this directory for why a fworker BUILD-lane agent can't
# run this itself).
#
# Usage: install-victorialogs.sh <tailscale-ip-of-this-host>
set -euo pipefail

VL_LISTEN_ADDR="${1:?usage: install-victorialogs.sh <tailscale-ip-of-bes>}"
VERSION="1.51.1"
ARCH="linux-amd64"
TARBALL="victoria-logs-${ARCH}-v${VERSION}.tar.gz"
BASE_URL="https://github.com/VictoriaMetrics/VictoriaLogs/releases/download/v${VERSION}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "== downloading VictoriaLogs v${VERSION} =="
curl -sSL -o "${WORKDIR}/${TARBALL}" "${BASE_URL}/${TARBALL}"
curl -sSL -o "${WORKDIR}/checksums.txt" "${BASE_URL}/victoria-logs-${ARCH}-v${VERSION}_checksums.txt"
( cd "$WORKDIR" && grep " ${TARBALL}\$" checksums.txt | sha256sum -c - )

echo "== unpacking =="
tar xzf "${WORKDIR}/${TARBALL}" -C "$WORKDIR"
install -m 755 -o root -g root "${WORKDIR}/victoria-logs-prod" /usr/local/bin/victoria-logs-prod

echo "== system user + data dir =="
id -u victorialogs >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin victorialogs
mkdir -p /var/lib/victorialogs
chown victorialogs:victorialogs /var/lib/victorialogs
chmod 750 /var/lib/victorialogs

echo "== systemd unit (binds ${VL_LISTEN_ADDR}:9428 — tailnet-scoped, matches wq#394's convention of never binding fleet services to 0.0.0.0) =="
sed "s/__VL_LISTEN_ADDR__/${VL_LISTEN_ADDR}/g" "$(dirname "$0")/victorialogs.service.template" > /etc/systemd/system/victorialogs.service
systemctl daemon-reload
systemctl enable --now victorialogs

echo "== firewall (ufw, if active) =="
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow in on tailscale0 to any port 9428 proto tcp comment 'wq#857 VictoriaLogs ingest'
fi

sleep 2
systemctl --no-pager status victorialogs | head -10
echo "== done: VictoriaLogs listening at http://${VL_LISTEN_ADDR}:9428, 30d retention, data in /var/lib/victorialogs =="
echo "Rollback: systemctl disable --now victorialogs && rm -f /usr/local/bin/victoria-logs-prod /etc/systemd/system/victorialogs.service && rm -rf /var/lib/victorialogs"
