#!/usr/bin/env bash
# wq#4206: install stealth-browser-mcp (github.com/vibheksoni/stealth-browser-mcp) on Lumen
# as a stdio MCP server for Claude Code sessions, behind the lumen_stealth_mcp.py guard.
#
# Runs as the lumen user (no root): `lumen-run start --name wq4206-install -- <this>`.
# Idempotent. Layout under $DEST:
#   repo/         upstream clone, checked out at $PIN (detached)
#   venv/         python 3.12 venv with upstream's pinned requirements.txt
#   browsers/     PLAYWRIGHT_BROWSERS_PATH: Chrome for Testing from playwright==$PLAYWRIGHT
#   uploads/ screenshots/ profiles/   the only dirs the guard lets tools touch
#   lumen_stealth_mcp.py  guard launcher (the registered MCP entrypoint)
#   lumen_stealth_guard.py, lumen_egress_proxy.py   its tool-argument and per-connection policy
#   PROVENANCE.json       what was installed, from where, with hashes
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

REPO=https://github.com/vibheksoni/stealth-browser-mcp
PIN=ba95cca933c8e90bfeaa515c1d05dad2927794e6
PLAYWRIGHT=1.63.0
ROOT=/home/lumen/.local/share/stealth-browser-mcp
DEST="$ROOT/${PIN:0:12}"
NAME=stealth-browser
CLAUDE=/home/lumen/.npm-global/bin/claude
UV=/home/lumen/.local/bin/uv
PY312=/home/lumen/.local/bin/python3.12
LAUNCHER="$DEST/lumen_stealth_mcp.py"
VPY="$DEST/venv/bin/python"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/lumen_stealth_mcp.py" "$LAUNCHER"
  vfile "$SRC/lumen_stealth_guard.py" "$DEST/lumen_stealth_guard.py"
  vfile "$SRC/lumen_egress_proxy.py" "$DEST/lumen_egress_proxy.py"
  [ -r "$DEST/PROVENANCE.json" ] || _vmiss "absent: $DEST/PROVENANCE.json"
  head=$(git -C "$DEST/repo" rev-parse HEAD 2>/dev/null || true)
  [ "$head" = "$PIN" ] || _vmiss "upstream checkout at '${head:-none}', want $PIN"
  [ -z "$(git -C "$DEST/repo" status --porcelain 2>/dev/null)" ] \
    || _vmiss "upstream checkout has local modifications: $DEST/repo"
  "$VPY" -I -c 'import fastmcp, nodriver' 2>/dev/null || _vmiss "venv cannot import fastmcp+nodriver: $VPY"
  vfile "$SRC/lumen-stealth-browser-chrome.apparmor" /etc/apparmor.d/lumen-stealth-browser-chrome
  chrome=$("$VPY" -I "$LAUNCHER" --print-browser 2>/dev/null || true)
  if [ -z "$chrome" ] || ! "$chrome" --version >/dev/null 2>&1; then
    _vmiss "browser not runnable: '${chrome:-none}'"
  elif ! timeout 30 "$chrome" --headless=new --dump-dom about:blank >/dev/null 2>&1; then
    _vmiss "browser aborts with its sandbox on (AppArmor userns profile not loaded? run install-apparmor.sh as root)"
  fi
  reg=$("$CLAUDE" mcp get "$NAME" 2>/dev/null || true)
  case "$reg" in
    *"$LAUNCHER"*--minimal*) ;;
    *) _vmiss "claude user-scope MCP '$NAME' not registered to $LAUNCHER --minimal" ;;
  esac
  vdone
fi

mkdir -p "$ROOT"
if [ ! -d "$DEST/repo/.git" ]; then
  git clone --quiet "$REPO" "$DEST/repo"
fi
git -C "$DEST/repo" fetch --quiet origin "$PIN" 2>/dev/null || true
git -C "$DEST/repo" -c advice.detachedHead=false checkout --quiet "$PIN"
[ "$(git -C "$DEST/repo" rev-parse HEAD)" = "$PIN" ]

[ -x "$VPY" ] || "$UV" venv --quiet --python "$PY312" "$DEST/venv"
VIRTUAL_ENV="$DEST/venv" "$UV" pip install --quiet --python "$VPY" -r "$DEST/repo/requirements.txt"

PLAYWRIGHT_BROWSERS_PATH="$DEST/browsers" \
  "$UV" tool run --quiet --from "playwright==$PLAYWRIGHT" playwright install chromium

mkdir -p "$DEST/uploads" "$DEST/screenshots" "$DEST/profiles"
install -m 0644 "$SRC/lumen_stealth_guard.py" "$DEST/lumen_stealth_guard.py"
install -m 0644 "$SRC/lumen_egress_proxy.py" "$DEST/lumen_egress_proxy.py"
install -m 0755 "$SRC/lumen_stealth_mcp.py" "$LAUNCHER"

chrome=$("$VPY" -I "$LAUNCHER" --print-browser)
"$VPY" -I - "$DEST" "$REPO" "$PIN" "$PLAYWRIGHT" "$chrome" "$("$chrome" --version)" \
  "$(VIRTUAL_ENV="$DEST/venv" "$UV" pip freeze --python "$VPY")" < "$SRC/write_provenance.py"

"$CLAUDE" mcp remove -s user "$NAME" >/dev/null 2>&1 || true
"$CLAUDE" mcp add-json -s user "$NAME" \
  "{\"type\":\"stdio\",\"command\":\"$VPY\",\"args\":[\"-I\",\"$LAUNCHER\",\"--minimal\"],\"env\":{}}"

exec "$0" --verify
