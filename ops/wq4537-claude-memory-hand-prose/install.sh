#!/usr/bin/env bash
# wq#4537 claude_memory.md hand-prose region. Installs the patched context_builder.py
# into minerva/memory (untracked there, so no repo to land it in), restores the prose the
# 10-08 cutover wiped, and corrects the two Lumen docs that send prose into the
# regenerated file. Run as lumen from a session with minerva write access: BUILD
# workers are walled out of /home/lumen/minerva (guard.sh, wq#1719/wq#900).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva/memory/context_builder.py
PROSE=/home/lumen/minerva/context/claude_memory_prose_2026-10-01.md
PROSE_SRC=/home/lumen/claude_memory.md.bak.1790821395

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/context_builder.py" "$LIVE"
  vcontains "$PROSE" "SESSION 2026-09-08"
  vcontains /home/lumen/claude_memory.md "^<!-- hand-prose:end -->"
  python3 "$SRC/apply_docs.py" --check || _vmiss "doc edits (apply_docs.py --check)"
  vdone
fi

cp -p "$LIVE" "$LIVE.bak.$(date +%Y%m%d)_wq4537"
install -m 0644 "$SRC/context_builder.py" "$LIVE"

if [ ! -s "$PROSE" ]; then
  sed -n '/SESSION 2026-09-08/,$p' "$PROSE_SRC" > "$PROSE.tmp"
  [ -s "$PROSE.tmp" ] || { rm -f "$PROSE.tmp"; echo "no SESSION 2026-09-08 block in $PROSE_SRC" >&2; exit 1; }
  mv "$PROSE.tmp" "$PROSE"
fi

python3 "$SRC/apply_docs.py"
python3 "$LIVE" --out /home/lumen/claude_memory.md
bash "$0" --verify
