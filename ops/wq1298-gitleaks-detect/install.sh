#!/usr/bin/env bash
# wq#1298 gitleaks-detect install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq1298-gitleaks-detect/install.sh
# (this repo's convention for privileged unit installs — see ops/wq900-primary-tree-guard).
#
# Builds gitleaks from source (go build, no docker, same pattern as
# ops/wq1184-gatus-health-checks). Resolves the latest release tag at
# install time via git ls-remote instead of a hardcoded version, since this
# script has no way to verify a pinned version is still current. Idempotent:
# safe to re-run (skips the build if the binary already exists for that tag).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="/home/lumen/build/gitleaks"
BIN_DEST="/home/lumen/bin/gitleaks"
CRON_DEST="/home/lumen/bin/gitleaks-detect-cron"

echo "== resolving latest gitleaks release tag =="
GITLEAKS_TAG=$(sudo -u lumen git ls-remote --tags --refs --sort='-v:refname' \
  https://github.com/gitleaks/gitleaks.git 'v*' | head -1 | sed 's#.*refs/tags/##')
[ -n "$GITLEAKS_TAG" ] || { echo "could not resolve a gitleaks release tag" >&2; exit 1; }
echo "target: $GITLEAKS_TAG"

echo "== building gitleaks ${GITLEAKS_TAG} (go build, no docker) =="
sudo -u lumen mkdir -p "$(dirname "$BUILD_DIR")"
if [ ! -d "$BUILD_DIR/.git" ]; then
  sudo -u lumen git clone --branch "$GITLEAKS_TAG" --depth 1 \
    https://github.com/gitleaks/gitleaks.git "$BUILD_DIR"
else
  sudo -u lumen git -C "$BUILD_DIR" fetch --depth 1 origin "$GITLEAKS_TAG"
  sudo -u lumen git -C "$BUILD_DIR" checkout "$GITLEAKS_TAG"
fi
sudo -u lumen bash -c "cd '$BUILD_DIR' && go build -o '$BIN_DEST' ."
chmod 755 "$BIN_DEST"
"$BIN_DEST" version || true

echo "== deploying cron script =="
sudo -u lumen "$SRC/../wq2856-atomic-install/install-script" -m 0755 "$SRC/gitleaks_detect.py" "$CRON_DEST"

echo "== installing systemd units =="
cp "$SRC/gitleaks-detect.service" "$SRC/gitleaks-detect.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gitleaks-detect.timer

echo "gitleaks-detect.timer installed; first run at boot+15min, then daily"
echo "manual run:   systemctl start gitleaks-detect.service"
echo "dry run:      python3 $CRON_DEST run --dry-run"
echo "logs:         journalctl -u gitleaks-detect.service -n 100 --no-pager"
echo "state:        /home/lumen/logs/.gitleaks-detect.state.json (per-repo known fingerprints)"
