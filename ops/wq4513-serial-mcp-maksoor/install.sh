#!/bin/bash
# wq#4513 — installs qarnet/serial-mcp (Rust, MIT) pinned at tag v0.9.3 on
# Maksoor, built from source, HTTP transport bound to Maksoor's Tailscale IP.
#
# Run this ON MAKSOOR as the `maksoor` user. This worker's sandbox has no
# execution path onto Maksoor (see README.md "Blocker" section) — this
# script was written and reviewed here but not executed.
set -euo pipefail

REPO_TAG="v0.9.3"
SRC_DIR="/opt/serial-mcp/src"
BIN_DIR="/opt/serial-mcp/bin"
CAPTURE_DIR="/var/log/serial-mcp"
TAILSCALE_IP="100.81.204.74"  # re-verify with `tailscale ip -4` before relying on this

echo "[serial-mcp] Target bind address: $TAILSCALE_IP:8000 — re-check with 'tailscale ip -4' if this drifted."

if ! command -v cargo &>/dev/null; then
    echo "[serial-mcp] cargo not found, installing rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
fi

echo "[serial-mcp] cargo: $(cargo --version)"

sudo mkdir -p "$(dirname "$SRC_DIR")" "$BIN_DIR" "$CAPTURE_DIR"
sudo chown "$(id -u):$(id -g)" "$(dirname "$SRC_DIR")" "$BIN_DIR" "$CAPTURE_DIR"

if [ -d "$SRC_DIR/.git" ]; then
    echo "[serial-mcp] Source checkout exists, fetching tag $REPO_TAG..."
    git -C "$SRC_DIR" fetch --tags
    git -C "$SRC_DIR" checkout "$REPO_TAG"
else
    echo "[serial-mcp] Cloning qarnet/serial-mcp at $REPO_TAG (pinned, not main)..."
    git clone --branch "$REPO_TAG" --depth 1 \
        https://github.com/qarnet/serial-mcp.git "$SRC_DIR"
fi

echo "[serial-mcp] Building (cargo build --release --locked)..."
(cd "$SRC_DIR" && cargo build --release --locked)

install -m 755 "$SRC_DIR/target/release/serial-mcp" "$BIN_DIR/serial-mcp"

if ! id -nG "$(whoami)" | grep -qw dialout; then
    echo "[serial-mcp] Adding $(whoami) to dialout group for port access..."
    sudo usermod -aG dialout "$(whoami)"
    echo "[serial-mcp] NOTE: log out/in (or reboot) for the group change to take effect."
fi

echo "[serial-mcp] Installing systemd unit..."
sudo cp "$(dirname "$0")/serial-mcp.service" /etc/systemd/system/serial-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now serial-mcp

echo "[serial-mcp] Install complete. Verify with: curl http://$TAILSCALE_IP:8000/"
