#!/usr/bin/env bash
# wq#1557 — pin the self-hosted Renovate (ops/wq859-renovate-gitea) npm
# global install to 44.69.11 (APK version-constraint fix). Runs as lumen,
# no root — same prerequisite path as ops/wq859-renovate-gitea/README.md
# ("Prerequisite (one-time, as lumen, no root)"): the renovate-gitea.timer
# starts a fresh renovate.js process each run against whatever is on PATH
# under /home/lumen/.npm-global, so no service restart is needed after this.
#
#   /home/lumen/bin/lumen-run start -- bash \
#       /home/lumen/foreman/ops/wq1557-renovate-upgrade/upgrade.sh
set -euo pipefail

RENOVATE_VERSION=44.69.11

npm install -g "renovate@${RENOVATE_VERSION}"

installed="$(node -p "require('/home/lumen/.npm-global/lib/node_modules/renovate/package.json').version")"
if [ "$installed" != "$RENOVATE_VERSION" ]; then
  echo "expected renovate ${RENOVATE_VERSION}, got ${installed}" >&2
  exit 1
fi
echo "renovate ${installed} installed globally; next renovate-gitea.service run picks it up"
