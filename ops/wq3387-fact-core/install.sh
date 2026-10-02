#!/usr/bin/env bash
# wq#3387 — install FACT (Firmware Analysis and Comparison Tool, fkie-cad/FACT_core)
# on Lumen for automated extraction + analysis + diff of firmware images.
#
# Steps below are transcribed from the upstream install guide (verified live,
# not guessed): https://github.com/fkie-cad/FACT_core/blob/master/INSTALL.md
# and https://github.com/fkie-cad/FACT_core/blob/master/src/install/pre_install.sh
# FACT ships no apt package; its own installer (`pre_install.sh` + `install.py`)
# removes/installs system packages, installs Docker from Docker's own apt repo,
# and adds the invoking user to the `docker` group, so it must run as a non-root
# user who already holds (or is granted) passwordless-capable sudo — never as root
# itself, and never as a Lumen automation account.
#
#   install.sh --verify   read-only (wq#1696 contract): clone present, venv
#                         present, a FACT-labelled container is up, and the web
#                         UI answers on its port. FACT_INSTALL_DIR, FACT_VENV_DIR
#                         and FACT_PORT override the defaults (used by the test
#                         suite; defaults are $HOME/FACT_core, $HOME/venv-fact,
#                         5000 — matching upstream's documented layout).
#   install.sh             install on Lumen, as the `lumen` user with sudo rights.
#                         Idempotent: skips the clone/venv steps if already done.
#                         Runs pre_install.sh (installs Docker, adds `lumen` to the
#                         docker group) then install.py inside a `newgrp docker`
#                         subshell so the new group membership applies without a
#                         reboot (the documented no-reboot path in INSTALL.md).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

REPO_URL="https://github.com/fkie-cad/FACT_core.git"
INSTALL_DIR="${FACT_INSTALL_DIR:-$HOME/FACT_core}"
VENV_DIR="${FACT_VENV_DIR:-$HOME/venv-fact}"
PORT="${FACT_PORT:-5000}"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  if [ -r "$INSTALL_DIR/.git/config" ]; then
    vcontains "$INSTALL_DIR/.git/config" "fkie-cad/FACT_core"
  else
    _vmiss "absent: $INSTALL_DIR (FACT_core clone)"
  fi

  if [ ! -r "$VENV_DIR/bin/activate" ]; then
    _vmiss "absent: $VENV_DIR (install venv)"
  fi

  if command -v docker >/dev/null 2>&1; then
    containers="$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null || true)"
    case "$containers" in
      *[Ff][Aa][Cc][Tt]*) ;;
      *) _vmiss "no running container name/image matches 'fact'" ;;
    esac
  else
    _vmiss "absent: docker"
  fi

  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
  case "$code" in
    200|302) ;;
    *) _vmiss "web UI not responding on 127.0.0.1:${PORT} (http ${code:-none})" ;;
  esac

  vdone
fi

# ---- install (on Lumen, as the `lumen` user — NOT root; see RUNBOOK.md) ----
[ "$(id -u)" != 0 ] || { echo "install: do not run as root — FACT's own installer needs a regular user with sudo rights (see RUNBOOK.md)" >&2; exit 2; }
command -v sudo >/dev/null || { echo "install: sudo not found" >&2; exit 2; }
command -v git >/dev/null || { echo "install: git not found — run: sudo apt update && sudo apt install -y git python3-venv" >&2; exit 2; }
command -v python3 >/dev/null || { echo "install: python3 not found" >&2; exit 2; }

if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

if [ ! -x "$VENV_DIR/bin/python3" ]; then
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"
bash "$INSTALL_DIR/src/install/pre_install.sh"

# Apply the new `docker` group membership without a reboot (INSTALL.md's
# documented no-restart path) and run FACT's own installer inside it.
newgrp docker <<END
. "$VENV_DIR/bin/activate"
python3 "$INSTALL_DIR/src/install.py"
END

echo "install: OK — start FACT with: $INSTALL_DIR/start_all_installed_fact_components"
echo "install: then open http://localhost:${PORT}/ for the web UI"
