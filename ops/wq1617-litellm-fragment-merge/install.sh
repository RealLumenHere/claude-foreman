#!/usr/bin/env bash
# wq#1617 — quarantine the stale full-file `.staged.*` copies of the live
# LiteLLM proxy config so nothing can apply one wholesale by accident, then
# point at the fragment-merge tool for anything worth keeping from them.
# Run from a session with write access to /home/lumen/agent (the gateway
# node) — this BUILD worker's sandbox has neither read nor write access to
# that path (see README.md).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
AGENT_DIR=/home/lumen/agent
ARCHIVE_DIR="$AGENT_DIR/staged-archive"
DATE_TAG=$(date -u +%Y%m%d)

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# This bundle deploys no file: it quarantines the stale full-file .staged.*
# copies of the live gateway config so none can be applied wholesale. The
# contract is therefore the absence of those copies, not the presence of ours,
# which is why the content index read it as ABSENT and wq#1732 filed it PENDING.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  if [ ! -d "$AGENT_DIR" ]; then
    echo "ERR unreadable: $AGENT_DIR (run this on the gateway node)"; _V_ERR=1; vdone
  fi
  n="$(find "$AGENT_DIR" -maxdepth 1 -name '*.staged.*' 2>/dev/null | wc -l || true)"
  [ "$n" = 0 ] || _vmiss "$n un-quarantined .staged.* config copy(ies) in $AGENT_DIR"
  [ -d "$ARCHIVE_DIR" ] || _vmiss "absent: $ARCHIVE_DIR"
  vdone
fi

sudo -u lumen mkdir -p "$ARCHIVE_DIR"

for f in "$AGENT_DIR"/litellm_config.yaml.staged.*; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    dest="$ARCHIVE_DIR/${base}.STALE-${DATE_TAG}"
    echo "quarantining $f -> $dest"
    sudo -u lumen mv "$f" "$dest"
done

cat > /tmp/wq1617-staged-archive-README.md <<'EOF'
# Quarantined staged LiteLLM configs (wq#1617)

These are full-file copies of `litellm_config.yaml` taken at some point in
the past and never applied. A full-file copy is a lost-update hazard by
construction: applying one wholesale silently reverts every change the live
config gained after the copy was taken (the triggering case:
`.staged.minimax-m3`, copied 2026-07-31, would have dropped the glm-5.3-flash
pricing wq#1591 added on 2026-09-10, sending the fleet's largest workload
back to logging $0.00 spend with no error).

Do not apply any file in this directory to the live config. If something in
one of them is still worth landing, extract it as a fragment and merge it
with `ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py merge`,
which refuses to overwrite a pricing key the live config already has. See
`ops/wq1617-litellm-fragment-merge/README.md` in the claude-foreman repo for
the tool and `fragments/minimax-m3.yaml` for the one candidate already
extracted from `.staged.minimax-m3`.
EOF
sudo -u lumen mv /tmp/wq1617-staged-archive-README.md "$ARCHIVE_DIR/README.md"

echo
echo "Remaining manual steps (see README.md 'Deploy checklist'):"
echo "  1. Before merging fragments/minimax-m3.yaml, verify its pricing comment"
echo "     against OpenRouter's current price list (it is a 2026-07-31 snapshot)."
echo "  2. python3 $HERE/merge_litellm_fragment.py merge $AGENT_DIR/litellm_config.yaml \\"
echo "         $HERE/fragments/minimax-m3.yaml -o /tmp/litellm_config.yaml.merged"
echo "  3. python3 $HERE/merge_litellm_fragment.py check $AGENT_DIR/litellm_config.yaml \\"
echo "         /tmp/litellm_config.yaml.merged   # must print OK before proceeding"
echo "  4. Review the diff, then replace $AGENT_DIR/litellm_config.yaml and reload the proxy."
echo "  5. Going forward, run 'merge_litellm_fragment.py check <live> <candidate>' before"
echo "     ANY 'cp candidate live' on this file — see README.md."
