#!/usr/bin/env bash
# wq#3389 — install angr (symbolic execution framework) for automated
# vulnerability discovery in extracted binaries, plus an `angr-python` shim
# in /home/lumen/bin so RE skills/scripts can run it without knowing the
# venv path. Lumen's system Python is externally managed (PEP 668: a bare
# `pip install angr` there refuses with "externally-managed-environment"),
# so angr lives in a dedicated venv instead of system site-packages — the
# install method angr's own PyPI page recommends (`mkvirtualenv ... &&
# python -m pip install angr`). The venv lands directly in /home/lumen/bin
# (gitignored, like the existing /trivy, /gatus, /renode-dist tool installs
# there) rather than a separate dotdir, matching how sibling re-tool
# bundles (retdec, routersploit, ...) already land their payloads.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
VENV=${VENV:-/home/lumen/bin/angr-venv}
BIN=${BIN:-/home/lumen/bin}

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/angr-python" "$BIN/angr-python"
  if [ ! -x "$VENV/bin/python3" ]; then
    _vmiss "absent: $VENV/bin/python3"
  else
    "$VENV/bin/python3" -c "import angr" >/dev/null 2>&1 || _vmiss "angr not importable in $VENV"
  fi
  vdone
fi

echo "== creating venv at $VENV =="
python3 -m venv "$VENV"
echo "== installing angr =="
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install angr
echo "== installing $BIN/angr-python shim =="
cp "$SRC/angr-python" "$BIN/angr-python"
chmod 0755 "$BIN/angr-python"
echo "== verifying import =="
"$BIN/angr-python" -c "import angr; print('angr', angr.__version__, 'import OK')"
