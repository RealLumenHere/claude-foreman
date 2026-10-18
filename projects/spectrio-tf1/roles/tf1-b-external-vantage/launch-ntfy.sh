#!/usr/bin/env bash
# wq#4181 — tf1-b: launch self-hosted ntfy 2.28, bound to the tailscale IP,
# ACL deny-all until a real credential is provisioned at bench time (see
# ntfy-server.yml). Run by ntfy.service.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TS_IP="$(tailscale ip -4)"

mkdir -p /var/lib/ntfy-cache /etc/ntfy
docker rm -f ntfy >/dev/null 2>&1 || true
exec docker run --name ntfy --rm \
  -v "$SRC/ntfy-server.yml:/etc/ntfy/server.yml:ro" \
  -v /var/lib/ntfy-cache:/var/cache/ntfy \
  -v /etc/ntfy:/etc/ntfy \
  -p "${TS_IP}:80:80" \
  binwiederhier/ntfy:2.28 serve
