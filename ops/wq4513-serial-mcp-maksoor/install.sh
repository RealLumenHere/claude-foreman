#!/bin/bash
# wq#4513 - install qarnet/serial-mcp v0.9.3 on Maksoor (HTTP bound to Tailscale IP only).
# Run ON MAKSOOR as user maksoor (NOPASSWD sudo). Idempotent. Run end to end (idempotent re-run verified) 2026-10-23
# by serial-mcp-2df1e9. Build uses rustup minimal profile + the pinned 1.97.1 toolchain.
set -euo pipefail
TAG=v0.9.3; WANT_SHA=e0f6df95cc073499447b84ec941cbb852660f637
SRC=/opt/serial-mcp/src; BIN=/opt/serial-mcp/bin; HERE=$(cd "$(dirname "$0")" && pwd)
TS_IP=$(tailscale ip -4 | head -1)
[ "$TS_IP" = "100.81.204.74" ] || { echo "Tailscale IP is $TS_IP, unit says 100.81.204.74: edit serial-mcp.service --bind first"; exit 1; }
sudo mkdir -p /opt/serial-mcp "$BIN" /var/log/serial-mcp
sudo chown "$(id -u):$(id -g)" /opt/serial-mcp "$BIN" /var/log/serial-mcp
if ! command -v cargo >/dev/null && [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.97.1
fi
. "$HOME/.cargo/env"
rustup toolchain list | grep -q '^1.97.1' || rustup toolchain install 1.97.1 --profile minimal
[ -d "$SRC/.git" ] || git clone --branch "$TAG" --depth 1 https://github.com/qarnet/serial-mcp.git "$SRC"
[ "$(git -C "$SRC" rev-parse HEAD)" = "$WANT_SHA" ] || { echo "source is not $TAG@$WANT_SHA, refusing"; exit 1; }
# Local patch: rmcp rejects non-localhost Host headers (403). See README.
if git -C "$SRC" apply --reverse --check "$HERE/allowed-hosts.patch" 2>/dev/null; then echo "patch already applied"
else git -C "$SRC" apply "$HERE/allowed-hosts.patch"; fi
# RUSTUP_TOOLCHAIN overrides the repo rust-toolchain.toml (which also wants rust-analyzer + an aarch64 target).
(cd "$SRC" && RUSTUP_TOOLCHAIN=1.97.1 nice -n 10 cargo build --release --locked)
[ -f "$BIN/serial-mcp" ] && cp -p "$BIN/serial-mcp" "$BIN/serial-mcp.prev"
install -m 755 "$SRC/target/release/serial-mcp" "$BIN/serial-mcp"
id -nG | grep -qw dialout || { echo "maksoor is not in dialout"; exit 1; }
sudo install -m 644 "$HERE/serial-mcp.service" /etc/systemd/system/serial-mcp.service
sudo systemd-analyze verify /etc/systemd/system/serial-mcp.service
sudo systemctl daemon-reload && sudo systemctl enable serial-mcp && sudo systemctl restart serial-mcp
sleep 3; systemctl is-active serial-mcp; ss -tlnp | grep ':8400'
echo "bind must show ONLY $TS_IP:8400 (never 0.0.0.0). Endpoint: http://$TS_IP:8400/mcp"
