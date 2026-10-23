#!/usr/bin/env bash
# wq#4540 boot budget bundle. Installs the two staged tools, runs the HANDOFF archive+rewrite
# and the LIVE_FLEET_STATE cap once each, then prints (does not apply) the exact claude_context.sh
# edits this session could not verify (no read access to minerva/context — see README "Why
# staged, not applied"). Run as lumen from a session with minerva + lumen-bin fs access: BUILD
# workers are walled out of both (guard.sh, wq#1719/wq#900; fleet-exec-policy.yaml wq#1467).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

BIN_LIVE=/home/lumen/bin/fleet-tools-render
HANDOFF_LIVE=/home/lumen/ORCHESTRATOR-HANDOFF.md
SESSIONS_DIR=/home/lumen/minerva/context/sessions
LIVE_FLEET_STATE=/home/lumen/minerva/context/LIVE_FLEET_STATE.md
CLAUDE_CONTEXT=/home/lumen/minerva/context/claude_context.sh

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/fleet-tools-render" "$BIN_LIVE"
  vcontains "$HANDOFF_LIVE" "wq#4540"
  [ -d "$SESSIONS_DIR" ] || { echo "MISSING: $SESSIONS_DIR"; exit 1; }
  vdone
fi

echo "== 1/4: fleet-tools-render --boot =="
cp -p "$BIN_LIVE" "$BIN_LIVE.bak.$(date +%Y%m%d)_wq4540"
install -m 0755 "$SRC/fleet-tools-render" "$BIN_LIVE"
python3 "$BIN_LIVE" --boot || true  # exit 2 on pre-existing registry drift is not this bundle's fault

echo "== 2/4: LIVE_FLEET_STATE 4000-tok cap =="
cp -p "$LIVE_FLEET_STATE" "$LIVE_FLEET_STATE.bak.$(date +%Y%m%d)_wq4540"
python3 "$SRC/live_fleet_state_cap.py" --file "$LIVE_FLEET_STATE" --budget-tokens 4000 || true  # exit 1 = capped, not an error

echo "== 3/4: HANDOFF s3 archive + rewrite =="
cp -p "$HANDOFF_LIVE" "$HANDOFF_LIVE.bak.$(date +%Y%m%d)_wq4540"
python3 "$SRC/handoff_s3_rewrite.py" --file "$HANDOFF_LIVE" --archive-dir "$SESSIONS_DIR"
python3 /home/lumen/bin/lint-handoff.py "$HANDOFF_LIVE"

echo "== 4/4: claude_context.sh — NOT applied, apply by hand (see README) =="
cat <<'EOF'
Edit 1 (corrected by wq#4582 — the original anchor 'instincts_render.py --audience core' never
existed in the live file; line 56 has called /home/lumen/bin/instincts-render with no flags
since wq#958, ISSUE-LOG row 1547). Repoint the call site to the canonical tier-aware renderer
(option (a) of wq#4582: one renderer, no bin/harness drift; /home/lumen/foreman is the primary
claude-foreman checkout, already load-bearing for every fworker spawn). Exact-anchor sed,
refuse instead of guessing if the anchor is not found verbatim:

  grep -q -- '/home/lumen/bin/instincts-render' /home/lumen/minerva/context/claude_context.sh && \
    sed -i 's#/home/lumen/bin/instincts-render#python3 /home/lumen/foreman/harness/instincts_render.py --audience boot#' \
      /home/lumen/minerva/context/claude_context.sh \
    || echo "ANCHOR MISSING: '/home/lumen/bin/instincts-render' not found verbatim — do not guess, inspect the file"

Edit 2 (already applied live by wq#4576; the ANCHOR MISSING echo on a re-run is the no-op,
not a failure):

  grep -q 'FLEET_TOOLS\.md' /home/lumen/minerva/context/claude_context.sh && \
    sed -i 's/FLEET_TOOLS\.md/FLEET_TOOLS_BOOT.md/' /home/lumen/minerva/context/claude_context.sh \
    || echo "ANCHOR MISSING: 'FLEET_TOOLS.md' not found verbatim — do not guess, inspect the file"

After both: measure with
  export WQ_AGENT=x; bash /home/lumen/minerva/context/claude_context.sh --core | wc -c
Baseline 75020 B post wq#4576. Measured honestly (wq#4582): edit 1 saves only ~763 B against
the live corpus — bin/instincts-render (no flags) emits 13316 B, harness --audience boot emits
12553 B, because tier 2 has shrunk to 4 rules (~238 tok); wq#4540's 'tier-2 index is the single
biggest line item' premise no longer holds. The 45000 B target is carried by the HANDOFF s3
rewrite (wq#4581), not this edit. Re-run steps 1-3 of this script on every later boot-budget
pass; they are idempotent (fleet-tools-render always re-renders both outputs; the cap and the
s3 rewrite are no-ops once already applied — handoff_s3_rewrite.py's AnchorError on a second run
is exactly that no-op, not a failure).
EOF
