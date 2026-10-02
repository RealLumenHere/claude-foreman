#!/usr/bin/env bash
# wq#3388 RouterSploit install — embedded-exploit framework for recovered/EOL
# routers (Broadcom RT-AX86U, Cisco EOL gear). No root, no systemd unit: this
# is a CLI invoked by hand per device, not a standing service.
#
# requirements.txt from upstream is missing two things it needs on this
# fleet's Python 3.14.4, neither caught by `import routersploit` alone since
# routersploit/__init__.py is empty — only `import routersploit.interpreter`
# (what rsf.py itself does) walks the real chain (kb/ISSUE-LOG.md #1158):
#   1. stdlib `telnetlib` was removed in Python 3.13 (PEP 594);
#      routersploit/core/exploit/shell.py falls back to
#      `import telnetlib3 as telnetlib` on ImportError, but upstream never
#      lists telnetlib3 as a dependency.
#   2. routersploit/resources/wordlists/__init__.py does `import
#      pkg_resources`, which a bare `python3 -m venv` has no reason to carry:
#      pip stopped bundling setuptools into new venvs, and setuptools >= 81
#      dropped pkg_resources entirely. Pinning `setuptools<81` restores it
#      (upstream's own deprecation notice names 81 as the cutoff).
# Installed into a dedicated venv rather than user/system site-packages so
# routersploit's pinned requests==2.32.2 can't collide with another fleet
# tool's pin — the system python3 already carries pkg_resources via the
# apt-installed python3-setuptools, which is exactly what let this gap hide
# during an earlier scratch test against the system interpreter instead of
# the venv's own.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
WORKDIR=/home/lumen/bin/routersploit
VENV="$WORKDIR/.venv"
WRAPPER=/home/lumen/bin/rsf

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# Imports routersploit.interpreter (not bare `routersploit`, whose __init__.py is
# empty and so never exercises the telnetlib3/pkg_resources chain) via -c, never by
# running rsf.py the script, so the check never touches rsf.py's module-level
# RotatingFileHandler("routersploit.log") — that write happens only when rsf.py
# itself is executed, not on import.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  [ -x "$VENV/bin/python3" ] || _vmiss "absent: $VENV/bin/python3"
  [ -f "$WORKDIR/rsf.py" ] || _vmiss "absent: $WORKDIR/rsf.py"
  vfile "$SRC/rsf-wrapper" "$WRAPPER"
  if [ -x "$VENV/bin/python3" ] && [ -f "$WORKDIR/rsf.py" ]; then
    if ! out="$(cd "$WORKDIR" && "$VENV/bin/python3" -c 'import routersploit.interpreter' 2>&1)"; then
      echo "ERR import routersploit.interpreter failed: $out"
      _V_ERR=1
    fi
  fi
  vdone
fi

mkdir -p "$(dirname "$WORKDIR")"
if [ ! -d "$WORKDIR/.git" ]; then
  git clone --depth 1 https://github.com/threat9/routersploit.git "$WORKDIR"
fi

python3 -m venv "$VENV"
"$VENV/bin/pip" install --disable-pip-version-check -q \
  -r "$WORKDIR/requirements.txt" telnetlib3 "setuptools<81"

cp "$SRC/rsf-wrapper" "$WRAPPER"
chmod 755 "$WRAPPER"

echo "== verifying import works (routersploit.interpreter, the real chain) =="
( cd "$WORKDIR" && "$VENV/bin/python3" -c "import routersploit.interpreter; print('routersploit.interpreter import OK')" )

echo "== verifying rsf.py starts (non-interactive -h) =="
( cd "$WORKDIR" && "$VENV/bin/python3" rsf.py -h )

echo "routersploit installed at $WORKDIR"
echo "run:  $WRAPPER                                   (interactive, needs a real TTY)"
echo "or:   $WRAPPER -m <module> -s \"<option> <value>\"   (non-interactive, scriptable)"
