#!/usr/bin/env bash
# wq#5244 — add Frank's missing GitHub SSH key(s) on thor, maksoor, ptah (LRA audit wq#4999).
#
# Run ON LUMEN (it uses Lumen's fleet key to reach each node):
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5244-frank-gh-keys/fix-frank-gh-keys.sh'          # dry run
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5244-frank-gh-keys/fix-frank-gh-keys.sh --apply'  # add missing
#
# Equivalent of `ssh-import-id gh:FrankStupak` on each node, but:
#   - reports WHICH of the two keys is missing per node before touching anything (dry run default);
#   - ADD-ONLY: never removes or rewrites an existing line; backs up authorized_keys first;
#   - FAIL-CLOSED on drift: the live github.com/FrankStupak.keys must equal the pinned set below,
#     otherwise nothing is written (a changed GitHub key set is Frank's call, not this script's).
# Thor: appending a line to ~/.ssh/authorized_keys does not restart/interrupt anything (Tier-0 #2).
set -euo pipefail

GH_USER="FrankStupak"
# Pinned 2026-11-22 from https://github.com/FrankStupak.keys (2 keys, both ed25519). Public keys.
PINNED_KEYS=(
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH+6pUc8Jtgp42jVU8Y2lgcz3EUU3XYPo/pGtPYYBBGR"
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH53I9o3FsArc0TqMVs82sBrN8kXxsYAmeFRE3akAtS8"
)
# user@tailscale-ip, from `python3 /home/lumen/minerva/lumen_state.py current <node>` 2026-11-22.
declare -A TARGETS=(
  [thor]="thor@100.112.151.100"
  [maksoor]="maksoor@100.81.204.74"
  [ptah]="lumen@100.78.207.56"
)
DEFAULT_NODES=(thor maksoor ptah)

APPLY=0
NODES=()
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) NODES+=("$arg") ;;
  esac
done
[ "${#NODES[@]}" -eq 0 ] && NODES=("${DEFAULT_NODES[@]}")

# 1. Drift check: live GitHub set must equal the pinned set (type+body, comments ignored).
if [ -n "${WQ5244_LIVE_KEYS_FILE:-}" ]; then   # test hook: offline stand-in for the GitHub fetch
  live_raw="$(cat "$WQ5244_LIVE_KEYS_FILE")"
else
  live_raw="$(curl -sf --max-time 15 "https://github.com/${GH_USER}.keys" || true)"
fi
live="$(printf '%s\n' "$live_raw" | awk 'NF>=2{print $1" "$2}' | sort -u)"
pinned="$(printf '%s\n' "${PINNED_KEYS[@]}" | sort -u)"
if [ "$live" != "$pinned" ]; then
  echo "ABORT: github.com/${GH_USER}.keys differs from the pinned set (or fetch failed); nothing written." >&2
  exit 3
fi

# 2. Per node: report missing keys; with --apply append them (backup first), then re-verify.
#    The remote body travels on ssh stdin (`bash -s -- <mode>`), pinned keys embedded,
#    so no nested quoting of the script into the ssh command line.
REMOTE_BODY='set -eu
mode="$1"; ak="$HOME/.ssh/authorized_keys"; ts="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"; [ -f "$ak" ] || { touch "$ak"; chmod 600 "$ak"; }
check() {
  miss=""
  while IFS= read -r k; do
    [ -n "$k" ] || continue
    body="${k#* }"; body="${body%% *}"
    if grep -qF -- "$body" "$ak"; then [ "$1" = quiet ] || echo "  present  ${k%% *} ...${body: -12}"
    else [ "$1" = quiet ] || echo "  MISSING  ${k%% *} ...${body: -12}"; miss="${miss}${k}
"; fi
  done <<< "$KEYS"
}
check loud
[ -n "$miss" ] || { echo "  ok: nothing to add"; exit 0; }
[ "$mode" = apply ] || { echo "  dry-run: not writing"; exit 0; }
cp -p "$ak" "$ak.bak.${ts}_wq5244"
printf "%s" "$miss" | sed "s/\$/ gh:FrankStupak wq5244/" >> "$ak"
chmod 600 "$ak"
check quiet
[ -z "$miss" ] || { echo "  ERROR: still missing after append" >&2; exit 4; }
echo "  added + re-verified; backup $ak.bak.${ts}_wq5244"'
remote_script="KEYS=\"$(printf '%s\n' "${PINNED_KEYS[@]}")\"
${REMOTE_BODY}"

mode=dry; [ "$APPLY" -eq 1 ] && mode=apply
rc=0
for node in "${NODES[@]}"; do
  dest="${TARGETS[$node]:-}"
  if [ -z "$dest" ]; then echo "$node: unknown node (known: ${!TARGETS[*]})" >&2; rc=2; continue; fi
  echo "== $node ($dest) mode=$mode"
  if [ -n "${WQ5244_LOCAL_HOME:-}" ]; then
    # Test hook: run the remote body locally against a scratch $HOME instead of ssh.
    runner=(env HOME="$WQ5244_LOCAL_HOME" bash -s -- "$mode")
  else
    runner=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$dest" bash -s -- "$mode")
  fi
  if ! printf '%s\n' "$remote_script" | "${runner[@]}"; then
    echo "  FAILED to reach/update $node" >&2; rc=1
  fi
done
exit "$rc"
