#!/usr/bin/env bash
# wq#3386 — install Rizin + Cutter RE framework on Lumen.
#
# Cutter is Rizin's official GUI (a radare2 fork); both ship as apt packages on
# Ubuntu/Debian. Ticket text pins the exact package names and verify commands
# (apt install rizin cutter; verify `rizin -v` and `cutter --version`).
#
#   install.sh --verify   read-only (wq#1696 contract): rizin and cutter are both
#                         installed and their version commands succeed.
#   install.sh             install on Lumen, as root. Idempotent: a second run
#                         changes nothing. Never upgrades an already-installed
#                         package and never reboots.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if command -v rizin >/dev/null 2>&1; then
    rizin -v >/dev/null 2>&1 || _vmiss "rizin -v failed"
  else
    _vmiss "absent: rizin"
  fi
  # Cutter ships no apt package on Ubuntu 26.04; it is live as the upstream
  # AppImage, /usr/local/bin/Cutter -> /opt/Cutter.AppImage (wq#3486). Lumen is
  # headless and the AppImage bundles only the xcb Qt plugin, so `--version`
  # cannot start without a display; `--appimage-version` proves the AppImage
  # runtime itself executes.
  cut="$(command -v cutter || command -v Cutter || true)"
  if [ -z "$cut" ]; then
    _vmiss "absent: cutter (neither cutter nor Cutter on PATH)"
  elif [[ "$(readlink -f "$cut")" == *.AppImage ]]; then
    "$cut" --appimage-version >/dev/null 2>&1 || _vmiss "$cut --appimage-version failed"
  else
    "$cut" --version >/dev/null 2>&1 || _vmiss "$cut --version failed"
  fi
  . "$SRC/../_retool_apt_install.sh"
  retool_verify_no_autostart rizin cutter
  vdone
fi

# ---- install (on Lumen, as root) ----
[ "$(id -u)" = 0 ] || { echo "install: run as root on Lumen (see RUNBOOK.md)" >&2; exit 2; }
command -v apt-get >/dev/null || { echo "install: apt-get not found" >&2; exit 2; }
if command -v needrestart >/dev/null && ! grep -rqsE "restart\}[[:space:]]*=[[:space:]]*'l'" /etc/needrestart/conf.d/; then
  echo "install: needrestart is not list-only here; an apt install could restart serving units. Install the zz-needrestart-list-only.conf first (wq#855)." >&2
  exit 2
fi

MISSING=""
dpkg -s rizin  >/dev/null 2>&1 || MISSING="$MISSING rizin"
dpkg -s cutter >/dev/null 2>&1 || MISSING="$MISSING cutter"
if [ -n "$MISSING" ]; then
  apt-get update -qq
  # shellcheck disable=SC2086
  sim="$(apt-get -s install $MISSING 2>&1)"
  if printf '%s\n' "$sim" | grep -q '^Remv'; then
    echo "install: apt simulation would REMOVE packages; refusing" >&2; printf '%s\n' "$sim" | grep '^Remv' >&2; exit 3
  fi
  up="$(printf '%s\n' "$sim" | sed -n 's/^\([0-9][0-9]*\) upgraded.*/\1/p' | head -1)"
  [ "${up:-x}" = 0 ] || { echo "install: apt simulation would UPGRADE ${up:-?} packages; refusing" >&2; exit 3; }
  # wq#3488: rizin/cutter are CLI/GUI tools, not daemons, but any apt install
  # can ship a .service this ticket never asked to run (wq#3380 — bettercap).
  # Route every re-tool apt install through the no-autostart guard rather than
  # trusting each package to come daemon-free.
  . "$SRC/../_retool_apt_install.sh"
  retool_apt_install $MISSING
fi

echo "install: OK — $(rizin -v | head -1); $(cutter --version 2>&1 | head -1)"
