#!/usr/bin/env bash
# wq#4913 — deploy rea-guard + version pin to maksoor, update Lumen's
# rea-maksoor MCP registration to route through the guard.
#
# Two-host bundle:
#   1. SSH to maksoor: install rea-guard, generate PROVENANCE.json, upgrade
#      rea-agents from 6.4.0 to 6.5.0 (the Lumen-pinned version).
#   2. On Lumen: patch the project-scope .mcp.json to call rea-guard instead
#      of the bare rea binary on the remote side.
#
# Like wq#4006: a sandboxed BUILD worker ships the bundle but does not run
# it against the live files. The actual deploy is a privileged step.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

MAKSOOR_HOST="${WQ4913_MAKSOOR_HOST:-maksoor@100.81.204.74}"
MAKSOOR_RE_DIR="${WQ4913_MAKSOOR_RE_DIR:-/home/maksoor/re}"
LUMEN_PROJECT_DIR="${WQ4913_LUMEN_PROJECT_DIR:-$HOME/re}"
REA_PACKAGE="rea-agents"
REA_VERSION="${WQ4913_REA_VERSION:-6.5.0}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  # Remote side: guard, provenance, version
  if ! vremote_up "$MAKSOOR_HOST"; then
    echo "ERR maksoor unreachable — cannot verify remote state"
    exit 2
  fi

  vremote_file "$MAKSOOR_HOST" "$SRC/rea-guard" "$MAKSOOR_RE_DIR/bin/rea-guard"

  remote_version="$($SSH "$MAKSOOR_HOST" \
    "/home/maksoor/.npm-global/bin/rea --version 2>/dev/null || echo UNKNOWN" \
    2>/dev/null || echo UNREACHABLE)"
  case "$remote_version" in
    "$REA_VERSION"*) ;;
    *) _vmiss "maksoor rea version: got $remote_version, want $REA_VERSION" ;;
  esac

  remote_prov="$($SSH "$MAKSOOR_HOST" \
    "cat '$MAKSOOR_RE_DIR/PROVENANCE.json' 2>/dev/null || echo ABSENT" \
    2>/dev/null || echo UNREACHABLE)"
  case "$remote_prov" in
    *"\"version\""*"\"$REA_VERSION\""*) ;;
    *) _vmiss "maksoor PROVENANCE.json absent or wrong version" ;;
  esac

  # Lumen side: .mcp.json routes through guard
  if [ -r "$LUMEN_PROJECT_DIR/.mcp.json" ]; then
    vcontains "$LUMEN_PROJECT_DIR/.mcp.json" 'rea-guard'
  else
    _vmiss "absent: $LUMEN_PROJECT_DIR/.mcp.json"
  fi

  vdone
fi

echo "== wq#4913: deploying rea-guard + version pin to maksoor =="

# --- pre-flight: connectivity check ---
echo "checking maksoor connectivity..."
if ! $SSH "$MAKSOOR_HOST" true 2>/dev/null; then
  echo "FATAL: cannot reach $MAKSOOR_HOST — resolve connectivity first" >&2
  echo "  try: ping 100.81.204.74 / ping 192.168.8.140 (LAN)" >&2
  echo "  check: tailscale status | grep maksoor" >&2
  echo "  check: is maksoor powered on? lid open? wifi connected?" >&2
  exit 1
fi

# --- remote side: deploy guard, pin version ---
echo "deploying rea-guard to $MAKSOOR_HOST:$MAKSOOR_RE_DIR/bin/..."
$SSH "$MAKSOOR_HOST" "mkdir -p '$MAKSOOR_RE_DIR/bin'"
scp -o BatchMode=yes "$SRC/rea-guard" "$MAKSOOR_HOST:$MAKSOOR_RE_DIR/bin/rea-guard"
$SSH "$MAKSOOR_HOST" "chmod +x '$MAKSOOR_RE_DIR/bin/rea-guard'"

echo "checking current rea version on maksoor..."
current_version="$($SSH "$MAKSOOR_HOST" \
  "/home/maksoor/.npm-global/bin/rea --version 2>/dev/null || echo MISSING")"
echo "  current: $current_version, target: $REA_VERSION"

if [ "$current_version" != "$REA_VERSION" ]; then
  echo "upgrading rea-agents to $REA_VERSION on maksoor..."
  $SSH "$MAKSOOR_HOST" "npm install -g ${REA_PACKAGE}@${REA_VERSION}"
fi

echo "generating PROVENANCE.json on maksoor..."
scp -o BatchMode=yes "$SRC/../wq4006-rea-security-rescope-a1/generate_provenance.py" \
  "$MAKSOOR_HOST:/tmp/generate_provenance.py"
$SSH "$MAKSOOR_HOST" \
  "python3 /tmp/generate_provenance.py '$REA_PACKAGE' '$REA_VERSION' '$MAKSOOR_RE_DIR/PROVENANCE.json'"
$SSH "$MAKSOOR_HOST" "rm -f /tmp/generate_provenance.py"

# --- Lumen side: update .mcp.json to route through guard ---
echo "updating Lumen .mcp.json to route rea-maksoor through guard..."
if [ -r "$LUMEN_PROJECT_DIR/.mcp.json" ]; then
  backup="$LUMEN_PROJECT_DIR/.mcp.json.pre-wq4913-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$LUMEN_PROJECT_DIR/.mcp.json" "$backup"
  echo "  backed up -> $backup"
fi
python3 "$SRC/patch_mcp_json.py" "$LUMEN_PROJECT_DIR/.mcp.json" \
  "$MAKSOOR_RE_DIR/bin/rea-guard"

echo "== done: rea-guard deployed to maksoor, .mcp.json updated =="
echo "verify with: bash $SRC/install.sh --verify"
