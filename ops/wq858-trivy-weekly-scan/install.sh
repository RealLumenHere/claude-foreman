#!/usr/bin/env bash
# wq#858 Trivy install. Run as root via `lumen-run --root` on Lumen itself —
# this repo's convention (see ops/wq859-renovate-gitea, ops/wq857-victorialogs-bes)
# for a build-worker sandbox whose guard.sh unconditionally denies every
# root-elevation verb, so this script can be staged but not executed there.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/trivy-scan
VERSION="0.74.0"
ARCH="Linux-64bit"
TARBALL="trivy_${VERSION}_${ARCH}.tar.gz"
BASE_URL="https://github.com/aquasecurity/trivy/releases/download/v${VERSION}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "== downloading Trivy v${VERSION} =="
curl -sSL -o "${WORKDIR}/${TARBALL}" "${BASE_URL}/${TARBALL}"
curl -sSL -o "${WORKDIR}/checksums.txt" "${BASE_URL}/trivy_${VERSION}_checksums.txt"
( cd "$WORKDIR" && grep " ${TARBALL}\$" checksums.txt | sha256sum -c - )

echo "== installing binary =="
tar xzf "${WORKDIR}/${TARBALL}" -C "$WORKDIR" trivy
install -m 755 -o root -g root "${WORKDIR}/trivy" /usr/local/bin/trivy

echo "== staging scan script + report generator =="
mkdir -p "$DEST"
install -m 755 "$SRC/trivy-scan.sh" "$DEST/trivy-scan.sh"
install -m 644 "$SRC/trivy_report.py" "$DEST/trivy_report.py"

echo "== priming offline vulnerability DB (first fetch; weekly refresh happens in trivy-scan.sh) =="
/usr/local/bin/trivy image --download-db-only

echo "== report directory =="
mkdir -p /home/lumen/maint/trivy
chown lumen:lumen /home/lumen/maint/trivy

echo "== systemd unit + weekly timer =="
cp "$SRC/trivy-scan.service" "$SRC/trivy-scan.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trivy-scan.timer

echo "== done: trivy-scan.timer enabled (weekly, up to 30min random delay, persistent across reboots) =="
echo "manual run: systemctl start trivy-scan.service"
echo "logs: journalctl -u trivy-scan.service -n 200 --no-pager"
echo "reports: /home/lumen/maint/trivy/"
echo "rollback: systemctl disable --now trivy-scan.timer && rm -f /etc/systemd/system/trivy-scan.{service,timer} /usr/local/bin/trivy && rm -rf ${DEST}"
