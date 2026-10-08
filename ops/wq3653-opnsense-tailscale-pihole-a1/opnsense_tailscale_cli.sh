#!/bin/sh
# wq#3653 — run ON the OPNsense console (FreeBSD, root or sudo) after the os-tailscale
# plugin is installed. NOT run from this build worker: the Topton is not in
# harness/fleet-exec-policy.yaml's ssh allowlist, so this sandbox cannot reach it.
# See RUNBOOK.md items 1, 2, 5, 7, 8 for what each step does and why.
#
# Usage: opnsense_tailscale_cli.sh <authkey>
# <authkey> is minted by Frank in the Tailscale admin console (RUNBOOK.md item 2) and
# passed in, never hardcoded here.

set -eu

AUTHKEY="${1:?usage: opnsense_tailscale_cli.sh <authkey>}"
LAN_SUBNET="192.168.8.0/24"

if ! command -v pkg >/dev/null 2>&1; then
    echo "not a FreeBSD/OPNsense console (no pkg) — refusing to run" >&2
    exit 1
fi

# Item 1: install the plugin (pulls in the real tailscaled/tailscale binaries).
pkg install -y os-tailscale

# Items 2, 5, 7, 8: one `up` call with full desired state — tailscale up does not merge
# flags across calls, so every flag this box needs has to be in the same invocation.
tailscale up \
    --authkey="$AUTHKEY" \
    --hostname=opnsense-topton \
    --advertise-routes="$LAN_SUBNET" \
    --advertise-exit-node \
    --accept-dns=false

echo "tailscale up issued. Routes/exit-node now need approval in the Tailscale admin" >&2
echo "console (RUNBOOK.md items 5 and 7) before peers will actually use them." >&2

tailscale status
