#!/usr/bin/env bash
# wq#4006 — rescope REA (rea-agents) off user-scope ~/.claude.json onto a
# project-scoped registration, pin the version with a provenance record,
# and install the self-update guard + hostile-input policy.
#
# Live targets are outside this worker's granted dirs (~/.claude.json,
# ~/re) and ~/.claude.json gates every Claude Code session on Lumen, so
# per ops/VERIFY_CONTRACT.md this is a privileged/non-worker apply step —
# a sandboxed BUILD worker ships the bundle but does not run it against the
# real files. WQ4006_CLAUDE_JSON / WQ4006_REA_PROJECT_DIR exist so tests
# can point every step at a temp fixture instead (see ops/tests/test_wq4006_verify.py).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

CLAUDE_JSON="${WQ4006_CLAUDE_JSON:-$HOME/.claude.json}"
PROJECT_DIR="${WQ4006_REA_PROJECT_DIR:-$HOME/re}"
REA_PACKAGE="rea-agents"
REA_VERSION="${WQ4006_REA_VERSION:-6.5.0}"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  vfile "$SRC/rea-guard" "$PROJECT_DIR/bin/rea-guard"
  vfile "$SRC/HOSTILE_INPUT_POLICY.md" "$PROJECT_DIR/common/tools/HOSTILE_INPUT_POLICY.md"

  if [ ! -r "$PROJECT_DIR/.mcp.json" ]; then
    _vmiss "absent: $PROJECT_DIR/.mcp.json"
  else
    vcontains "$PROJECT_DIR/.mcp.json" '"rea"[[:space:]]*:'
    vcontains "$PROJECT_DIR/.mcp.json" '"rea-maksoor"[[:space:]]*:'
    vcontains "$PROJECT_DIR/.mcp.json" 'bin/rea-guard'
  fi

  if [ ! -r "$PROJECT_DIR/PROVENANCE.json" ]; then
    _vmiss "absent: $PROJECT_DIR/PROVENANCE.json"
  else
    vcontains "$PROJECT_DIR/PROVENANCE.json" '"version"[[:space:]]*:[[:space:]]*"'
    vcontains "$PROJECT_DIR/PROVENANCE.json" '"integrity"[[:space:]]*:[[:space:]]*"sha'
  fi

  # The point of this ticket: rea/rea-maksoor must be ABSENT from user
  # scope, not present-and-matching. Fleet-wide exposure is the bug.
  if [ -r "$CLAUDE_JSON" ]; then
    out="$(python3 -c '
import json, sys
try:
    config = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"ERR {exc}")
    sys.exit(2)
servers = config.get("mcpServers", {})
present = [k for k in ("rea", "rea-maksoor") if k in servers]
if present:
    print("MISS still in user scope: " + ", ".join(present))
    sys.exit(1)
sys.exit(0)
' "$CLAUDE_JSON")" || true
    case "$out" in
      "ERR"*) echo "$out"; _V_ERR=1 ;;
      "MISS"*) _vmiss "${out#MISS }" ;;
      "") ;;
    esac
  fi

  vdone
fi

echo "== wq#4006: rescoping REA MCP registration =="

SLOCK_SCOPE="wq4006-rea-rescope"
if [ "${WQ4006_SKIP_SLOCK:-}" != 1 ]; then
  /home/lumen/bin/slock acquire "$SLOCK_SCOPE" --ttl 300 --holder "wq4006-install.sh" \
    --note "editing $CLAUDE_JSON (user-scope mcpServers) for wq#4006"
  trap '/home/lumen/bin/slock release "$SLOCK_SCOPE" --holder "wq4006-install.sh" || true' EXIT
fi

if [ -r "$CLAUDE_JSON" ]; then
  backup="$CLAUDE_JSON.pre-wq4006-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$CLAUDE_JSON" "$backup"
  echo "backed up $CLAUDE_JSON -> $backup"
  python3 "$SRC/remove_user_scope.py" "$CLAUDE_JSON" rea rea-maksoor
else
  echo "no $CLAUDE_JSON to rescope (already absent)"
fi

mkdir -p "$PROJECT_DIR/bin" "$PROJECT_DIR/common/tools"
cp "$SRC/rea-guard" "$PROJECT_DIR/bin/rea-guard"
chmod +x "$PROJECT_DIR/bin/rea-guard"
cp "$SRC/HOSTILE_INPUT_POLICY.md" "$PROJECT_DIR/common/tools/HOSTILE_INPUT_POLICY.md"

sed "s#{{PROJECT_DIR}}#$PROJECT_DIR#g" "$SRC/rea.mcp.json.project" > "$PROJECT_DIR/.mcp.json"
echo "wrote $PROJECT_DIR/.mcp.json (scope: project)"

python3 "$SRC/generate_provenance.py" "$REA_PACKAGE" "$REA_VERSION" "$PROJECT_DIR/PROVENANCE.json"

echo "== done: rea/rea-maksoor removed from user scope, project scope live at $PROJECT_DIR =="
