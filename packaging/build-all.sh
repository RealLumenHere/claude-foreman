#!/bin/sh
# wq#3947: build every Limpet .deb this tree currently packages. Run from
# anywhere; builds land as siblings of each package dir (dpkg-buildpackage's
# own convention), e.g. packaging/limpet-hald-build/*.deb one level up.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

for pkg in limpet-hald limpet-mcp-meta limpet-tools; do
    echo "== building $pkg =="
    (cd "$HERE/$pkg" && dpkg-buildpackage -us -uc -b)
done
