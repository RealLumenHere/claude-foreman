#!/usr/bin/env bash
# wq#3332 — run ON NEPHTHYS as the lumen user, from a copy of this kit dir. Installs
# chat-digest-local + the pull script to ~/bin and enables the hourly user timer.
# Do NOT run this on hathor: its 2 ollama slots are dedicated to the judge (wq#2796);
# see README.md "hathor contention note". hathor can still run chat-digest-local
# manually, ad hoc, outside this installer.
set -euo pipefail
KIT=$(dirname "$(readlink -f "$0")")
ROOT="$HOME/chat-digest-local"

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
