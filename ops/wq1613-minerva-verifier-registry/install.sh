#!/usr/bin/env bash
# Install lumen-verify (wq#1613): script + registry -> /home/lumen/bin and
# /home/lumen/minerva/verify, units -> systemd. Idempotent.
#
# wq#5155: this bundle is NOT the source of truth for the two files it installs.
#   lumen_verify.py  <- lumen-bin   origin/main:lumen-verify         (live: /home/lumen/bin/lumen-verify)
#   registry.yaml    <- minerva     origin/main:verify/registry.yaml (live: /home/lumen/minerva/verify/registry.yaml)
# Both are developed in those repos and go live when they land there. The copies here only
# feed this bundle's tests. The bundle copy fell 220 lines behind once and this script would
# have rolled both live files back, so install refuses while a copy differs from its canonical
# main, and --verify reports that drift. Sync with: bash install.sh --sync (then commit).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

CANON=(
  "lumen_verify.py|/home/lumen/bin|lumen-verify"
  "registry.yaml|/home/lumen/minerva|verify/registry.yaml"
)

# canon_diff COPY REPO PATH: prints nothing and returns 0 when the bundle copy is byte-identical
# to REPO's origin/main:PATH; else prints why and returns 1. Fetch is best effort (offline =
# compare against the last fetched origin/main).
canon_diff() {
  local copy="$HERE/$1" repo="$2" path="$3"
  git -C "$repo" fetch -q origin main 2>/dev/null || true
  if ! git -C "$repo" cat-file -e "origin/main:$path" 2>/dev/null; then
    echo "$1: cannot read $repo origin/main:$path"; return 1
  fi
  cmp -s "$copy" <(git -C "$repo" show "origin/main:$path") && return 0
  echo "$1 differs from canonical $repo origin/main:$path"; return 1
}

if [ "${1:-}" = --sync ]; then
  for c in "${CANON[@]}"; do
    IFS='|' read -r f repo path <<<"$c"
    git -C "$repo" fetch -q origin main 2>/dev/null || true
    git -C "$repo" show "origin/main:$path" > "$HERE/$f"
    echo "synced $f <- $repo origin/main:$path ($(git -C "$repo" rev-parse --short origin/main))"
  done
  exit 0
fi

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for c in "${CANON[@]}"; do
    IFS='|' read -r f repo path <<<"$c"
    why=$(canon_diff "$f" "$repo" "$path") || _vmiss "$why (wq#5155: run install.sh --sync)"
  done
  vfile "$HERE/lumen_verify.py" /home/lumen/bin/lumen-verify
  vfile "$HERE/registry.yaml" /home/lumen/minerva/verify/registry.yaml
  vunit_file "$HERE/lumen-state-verify.service" /etc/systemd/system/lumen-state-verify.service
  vunit_file "$HERE/lumen-state-verify.timer" /etc/systemd/system/lumen-state-verify.timer
  # wq#5173: install leaves the timer disabled while it is disabled (Frank gate wq#3750, 10-08
  # Phase-1 subtraction). --verify must agree with install, or this bundle fails forever on a
  # state its own installer chose. Enabled (or LUMEN_VERIFY_ENABLE_TIMER=1) -> must be enabled
  # AND active. Disabled -> GATED, but it must also be inactive (disabled-but-running is a MISS).
  if systemctl is-enabled --quiet lumen-state-verify.timer 2>/dev/null || [ "${LUMEN_VERIFY_ENABLE_TIMER:-0}" = 1 ]; then
    venabled lumen-state-verify.timer
    vactive lumen-state-verify.timer
  else
    case "$(systemctl is-active lumen-state-verify.timer 2>/dev/null || true)" in
      active|activating) _vmiss "lumen-state-verify.timer disabled but active (gate wq#3750 bypassed)" ;;
      *) echo "GATED lumen-state-verify.timer disabled (Frank gate wq#3750); LUMEN_VERIFY_ENABLE_TIMER=1 install.sh to enable" ;;
    esac
  fi
  vdone
fi

# wq#5155: never install a stale copy over the live file.
stale=0
for c in "${CANON[@]}"; do
  IFS='|' read -r f repo path <<<"$c"
  canon_diff "$f" "$repo" "$path" >&2 || stale=1
done
if [ "$stale" = 1 ]; then
  echo "REFUSED: bundle copy differs from its canonical repo; installing it would roll the live file back." >&2
  echo "         bash $HERE/install.sh --sync, rerun the tests, land the sync, then install." >&2
  exit 3
fi

python3 -m pytest -q "$HERE/test_lumen_verify.py" "$HERE/test_repair_clobbered_facts.py"

install -m 0755 "$HERE/lumen_verify.py" /home/lumen/bin/lumen-verify
mkdir -p /home/lumen/minerva/verify
install -m 0644 "$HERE/registry.yaml" /home/lumen/minerva/verify/registry.yaml
# wq#2192: after the fixed probes are in place, undo earlier clobbers (idempotent).
python3 "$HERE/repair_clobbered_facts.py" --apply
sudo install -m 0644 "$HERE/lumen-state-verify.service" /etc/systemd/system/lumen-state-verify.service
sudo install -m 0644 "$HERE/lumen-state-verify.timer" /etc/systemd/system/lumen-state-verify.timer
sudo systemctl daemon-reload
# wq#5155: the timer was disabled in the 2026-10-08 Phase-1 timer purge and re-enabling it is a
# Frank gate (wq#3750). A reinstall must not undo that silently.
if systemctl is-enabled --quiet lumen-state-verify.timer 2>/dev/null || [ "${LUMEN_VERIFY_ENABLE_TIMER:-0}" = 1 ]; then
  sudo systemctl enable --now lumen-state-verify.timer
  systemctl is-active --quiet lumen-state-verify.timer
  systemctl list-timers lumen-state-verify.timer --no-pager --no-legend
else
  echo "lumen-state-verify.timer left disabled (Frank gate wq#3750); LUMEN_VERIFY_ENABLE_TIMER=1 to enable." >&2
fi

/home/lumen/bin/lumen-verify sweep --dry-run --json
