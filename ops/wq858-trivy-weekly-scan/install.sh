#!/usr/bin/env bash
# wq#858 Trivy install. Run as root via `lumen-run --root` on Lumen itself —
# this repo's convention (see ops/wq859-renovate-gitea, ops/wq857-victorialogs-bes)
# for a build-worker sandbox whose guard.sh unconditionally denies every
# root-elevation verb, so this script can be staged but not executed there.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/trivy-scan

# SUPERSEDED 2026-09-28 (wq#2985, triage of wq#1732's 36 untracked PENDING bundles).
#
# The weekly scan IS live -- lumen-trivy-weekly.timer is enabled+active on
# v0.74.0, per this ticket's own done notes -- but under a different unit name
# than the trivy-scan.service/.timer this bundle ships. The content index looked
# for the shipped names, found nothing, and filed the bundle as a pending
# deploy. Running it would install a SECOND weekly scanner alongside the live
# one and overwrite /usr/local/bin/trivy mid-flight.
#
# To re-adopt these unit names, first disable lumen-trivy-weekly.timer, then set
# WQ858_FORCE_SUPERSEDED_DEPLOY=1.

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# For a superseded bundle "correct" is the replacement being live and the
# shipped names staying absent.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  venabled lumen-trivy-weekly.timer
  vactive  lumen-trivy-weekly.timer
  for u in trivy-scan.service trivy-scan.timer; do
    if [ -e "/etc/systemd/system/$u" ]; then
      _vmiss "superseded unit present: /etc/systemd/system/$u (lumen-trivy-weekly owns this)"
    fi
  done
  vdone
fi

if [ "${WQ858_FORCE_SUPERSEDED_DEPLOY:-0}" != "1" ]; then
  echo "refuse: wq#858 is SUPERSEDED (wq#2985) -- lumen-trivy-weekly.timer already runs" >&2
  echo "        this scan. See the header for how to re-adopt these unit names." >&2
  exit 2
fi
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
