#!/usr/bin/env bash
# wq#4588: tag_sections.py rewrites section files in place instead of unlinking them
# all first, so context_selector.py (run by claude_context.sh --core straight after
# the claude_memory.md rewrite that fires minerva-tag-sections.path) never reads ''
# for a section mid re-tag. Installs into minerva/context (untracked there, walled
# from BUILD workers by guard.sh WRITE_PRIMARY_TREES — same wall as wq#4537/wq#4553).
# Run as lumen from a session with minerva write access.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva/context/tag_sections.py

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/tag_sections.py" "$LIVE"
  vdone
fi

# Refuse if the live tagger moved on from the copy this fix was built against.
if ! cmp -s "$SRC/fixtures/tag_sections.orig.py" "$LIVE" && ! cmp -s "$SRC/tag_sections.py" "$LIVE"; then
  echo "install.sh: REFUSING — $LIVE differs from fixtures/tag_sections.orig.py;" >&2
  echo "  rebase the wq#4588 change onto the live file before installing." >&2
  exit 2
fi

cp -p "$LIVE" "$LIVE.bak.$(date +%Y%m%d)_wq4588"
install -m 0644 "$SRC/tag_sections.py" "$LIVE"
python3 -m pytest -q /home/lumen/minerva/context/test_tag_sections_secrets.py
python3 -m pytest -q "$SRC"
bash "$0" --verify

# Acceptance: two back-to-back --core runs agree to within the HANDOFF/live-state
# re-render noise (hundreds of bytes), not the ~41 KB of dropped sections. Each run
# rewrites claude_memory.md and so fires a re-tag that overlaps the next run's selector.
a=$(bash /home/lumen/minerva/context/claude_context.sh --core | wc -c)
b=$(bash /home/lumen/minerva/context/claude_context.sh --core | wc -c)
echo "--core sizes: $a $b"
