#!/usr/bin/env bash
# wq#5243 — install missing baseline fleet packages found by the LRA audit (wq#4999, 2026-11-21):
#   thoth: tmux   ptah: tmux   j5005-2: tmux + git
#
# Run ON LUMEN (it uses Lumen's fleet keys to reach each node):
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5243-baseline-pkgs/install-baseline-pkgs.sh'          # dry run
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq5243-baseline-pkgs/install-baseline-pkgs.sh --apply'  # install missing
#   optional trailing node names limit the run, e.g. `... --apply j5005-2`
#
# Behaviour:
#   - dry run (default) reports present/MISSING per package per node; writes nothing;
#   - --apply installs ONLY the missing packages, via passwordless root on the node (-n: fails fast,
#     never hangs on a password prompt), after an apt simulation that must show 0 removals and
#     0 upgrades (fail-closed: exit 3, nothing installed);
#   - NEEDRESTART_MODE=l keeps needrestart list-only (needrestart >= 3.0 honours the env var over
#     its config), so the install can never restart a serving unit. Nothing here reboots;
#   - re-verifies after install (dpkg status + `tmux -V` / `git --version`).
# Thor is not a target (Tier-0 #2).
set -euo pipefail

# Targets from `python3 /home/lumen/minerva/lumen_state.py current <node>` 2026-11-22.
#   ptah: lumen_state ssh_user note says the tailnet path needs interactive Tailscale-SSH
#         approval -> use the LAN address for scripts (lumen@192.168.8.125).
#   thoth: key /home/lumen/.ssh/thoth_ed25519 (lumen_state thoth.status, NOPASSWD sudo ok).
#   j5005-2: ssh_user recorded as jj5005-2 (UNVERIFIABLE in lumen_state); override with
#            WQ5243_DEST_j5005_2=user@host if that record is wrong.
declare -A TARGETS=(
  [thoth]="${WQ5243_DEST_thoth:-thoth@100.75.1.108}"
  [ptah]="${WQ5243_DEST_ptah:-lumen@192.168.8.125}"
  [j5005-2]="${WQ5243_DEST_j5005_2:-jj5005-2@100.115.88.13}"
)
declare -A IDENTITY=(
  [thoth]="/home/lumen/.ssh/thoth_ed25519"
)
declare -A PKGS=(
  [thoth]="tmux"
  [ptah]="tmux"
  [j5005-2]="tmux git"
)
DEFAULT_NODES=(thoth ptah j5005-2)

APPLY=0
NODES=()
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) NODES+=("$arg") ;;
  esac
done
[ "${#NODES[@]}" -eq 0 ] && NODES=("${DEFAULT_NODES[@]}")

# Remote body travels on ssh stdin (`bash -s -- <mode> <pkg>...`): no nested quoting.
read -r -d '' REMOTE_BODY <<'REMOTE' || true
set -eu
mode="$1"; shift
installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }
scan() {
  miss=""
  for p in "$@"; do
    if installed "$p"; then [ "$quiet" = 1 ] || echo "  present  $p"
    else [ "$quiet" = 1 ] || echo "  MISSING  $p"; miss="$miss $p"; fi
  done
}
quiet=0; scan "$@"
[ -n "$miss" ] || { echo "  ok: nothing to install"; exit 0; }
[ "$mode" = apply ] || { echo "  dry-run: not installing"; exit 0; }
SU="sudo -n"; [ "$(id -u)" = 0 ] && SU=""
$SU true 2>/dev/null || { echo "  ERROR: passwordless root unavailable on $(hostname); install by hand:$miss" >&2; exit 5; }
APT="$SU env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get"
$APT update -qq
sim="$($APT -s install $miss 2>&1)"
if printf '%s\n' "$sim" | grep -q '^Remv'; then
  echo "  ABORT: apt simulation would REMOVE packages; nothing installed" >&2
  printf '%s\n' "$sim" | grep '^Remv' >&2; exit 3
fi
up="$(printf '%s\n' "$sim" | sed -n 's/^\([0-9][0-9]*\) upgraded.*/\1/p' | sed -n 1p)"
[ "${up:-x}" = 0 ] || { echo "  ABORT: apt simulation would UPGRADE ${up:-?} packages; nothing installed" >&2; exit 3; }
$APT install -y $miss
quiet=1; scan $miss
[ -z "$miss" ] || { echo "  ERROR: still missing after install:$miss" >&2; exit 4; }
for p in "$@"; do
  case "$p" in
    tmux) echo "  verified $(tmux -V)" ;;
    git)  echo "  verified $(git --version)" ;;
  esac
done
REMOTE

mode=dry; [ "$APPLY" -eq 1 ] && mode=apply
rc=0
for node in "${NODES[@]}"; do
  dest="${TARGETS[$node]:-}"
  if [ -z "$dest" ]; then echo "$node: unknown node (known: ${!TARGETS[*]})" >&2; rc=2; continue; fi
  # shellcheck disable=SC2206
  pkgs=(${PKGS[$node]})
  echo "== $node ($dest) mode=$mode pkgs=${pkgs[*]}"
  if [ -n "${WQ5243_LOCAL:-}" ]; then
    # Test hook: run the remote body locally (PATH carries stubs) instead of ssh.
    runner=(bash -s -- "$mode" "${pkgs[@]}")
  else
    ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)
    [ -n "${IDENTITY[$node]:-}" ] && ssh_opts+=(-i "${IDENTITY[$node]}")
    runner=(ssh "${ssh_opts[@]}" "$dest" bash -s -- "$mode" "${pkgs[@]}")
  fi
  if ! printf '%s\n' "$REMOTE_BODY" | "${runner[@]}"; then
    echo "  FAILED on $node" >&2; rc=1
  fi
done
exit "$rc"
