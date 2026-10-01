#!/usr/bin/env bash
# wq#3332 — run ON NEPHTHYS as the lumen user, from a copy of this kit dir. Installs
# chat-digest-local + the pull script to ~/bin and enables the hourly user timer.
# Do NOT run this on hathor: its 2 ollama slots are dedicated to the judge (wq#2796);
# see README.md "hathor contention note". hathor can still run chat-digest-local
# manually, ad hoc, outside this installer.
set -euo pipefail
KIT=$(dirname "$(readlink -f "$0")")
ROOT="$HOME/chat-digest-local"

# wq#3355: the payload lands on nephthys, so --verify (run on Lumen by ops-deploy-drift)
# checks it over ssh. A local-only handler would report a live install as absent (wq#2985),
# and nephthys being offline must read ERR, never MISS. The unit files are *user* units.
# Not verified here: the hathor timer, which this installer deliberately never wires
# (README "hathor contention note"), and bin/chat-export, which ships via bin/, not this kit.
NODE="${CHAT_DIGEST_NODE:-nephthys}"
if [ "${1:-}" = --verify ]; then
  . "$KIT/../_verify.sh"
  if vremote_up "$NODE"; then
    # vremote_file single-quotes the remote path, so neither ~ nor $HOME would expand there:
    # ask the node for the install user's home instead of hardcoding one (the installer
    # itself installs relative to $HOME, and the ssh alias decides which user that is).
    RH="$($_VSSH "$NODE" 'printf %s "$HOME"' 2>/dev/null || true)"
    if [ -z "$RH" ]; then
      echo "ERR $NODE: cannot resolve the install user's HOME"; _V_ERR=1
    else
      vremote_file "$NODE" "$KIT/chat-digest-local"        "$RH/bin/chat-digest-local"
      vremote_file "$NODE" "$KIT/chat-digest-pull.sh"      "$RH/bin/chat-digest-pull.sh"
      vremote_file "$NODE" "$KIT/chat-digest-pull.service" "$RH/.config/systemd/user/chat-digest-pull.service"
      vremote_file "$NODE" "$KIT/chat-digest-pull.timer"   "$RH/.config/systemd/user/chat-digest-pull.timer"
      vremote_user_unit "$NODE" chat-digest-pull.timer
    fi
  fi
  vdone
fi

command -v ollama >/dev/null || { echo "ollama not installed on this node" >&2; exit 2; }
command -v rsync >/dev/null || { echo "rsync not installed" >&2; exit 2; }

mkdir -p "$HOME/bin" "$ROOT/status" "$HOME/.config/systemd/user"

install -m 755 "$KIT/chat-digest-local" "$KIT/chat-digest-pull.sh" "$HOME/bin/"
install -m 644 "$KIT/chat-digest-pull.service" "$KIT/chat-digest-pull.timer" \
  "$HOME/.config/systemd/user/"

systemctl --user daemon-reload
systemctl --user enable --now chat-digest-pull.timer

echo "installed. First verification (run now):"
echo "  systemctl --user start chat-digest-pull.service && cat $ROOT/status/pull.json"
