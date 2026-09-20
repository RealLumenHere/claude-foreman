#!/usr/bin/env bash
# wq#2270 — install the token-engine daemon: tokeneng user, token-client group,
# secret store, hardened unit. Root step; run detached per instinct #5, same
# convention as ops/wq2265-simpill-watch/install.sh:
#
#   /home/lumen/bin/lumen-run start --root -- bash \
#       <repo>/token-engine/install.sh <repo>/token-engine
#
# $1 = the token-engine source dir of the checkout being installed (defaults to
# the canonical clone). Idempotent: safe to re-run.
set -euo pipefail

SRC="${1:-/home/lumen/foreman/token-engine}"
LIB=/usr/local/lib/token-engine
STATE=/var/lib/token-engine
EVENTS=/home/lumen/foreman/events
POLICY_SRC=/home/lumen/bin/fleet_exec_policy.py

# (a) OS user + client group. tokeneng is a system user with no shell; lumen
# joins token-client for socket access only (design §5.2).
getent group token-client >/dev/null || groupadd --system token-client
getent group tokeneng >/dev/null || groupadd --system tokeneng
getent passwd tokeneng >/dev/null || \
    useradd --system --home-dir "$STATE" --no-create-home \
            --shell /usr/sbin/nologin --gid tokeneng -G token-client tokeneng
usermod -aG token-client lumen

# Secret store: 0700 dir, 0600 files, tokeneng:tokeneng — uid lumen has no read
# path (design property 1). The canary exists so the EACCES gate has a concrete
# file to fail on; it contains no secret.
install -d -m 0700 -o tokeneng -g tokeneng "$STATE" "$STATE/secrets"
if [ ! -f "$STATE/secrets/.canary" ]; then
    echo "token-engine canary $(date -Is) — placeholder, not a secret" \
        > "$STATE/secrets/.canary"
fi
chown -R tokeneng:tokeneng "$STATE/secrets"
find "$STATE/secrets" -maxdepth 1 -type f -exec chmod 0600 {} +
chmod 0700 "$STATE/secrets"

# (T10, wq#2278) migrate ranks 5-8 + rank 3 into the store (design §7).
# Default is COPY-ONLY: the lumen-readable original stays until every consumer
# rides the broker. Re-run with CUTOVER=1 to remove the originals — that is the
# moment the gate "unreadable by uid lumen" becomes true for each rank.
CUTOVER="${CUTOVER:-0}"
migrate() { # $1=src $2=dst-name-under-secrets
    local src="$1" dst="$STATE/secrets/$2"
    if [ ! -f "$src" ]; then echo "migrate: $src absent — skipped"; return 0; fi
    install -D -m 0600 -o tokeneng -g tokeneng "$src" "$dst"
    echo "migrated $src -> $dst"
    if [ "$CUTOVER" = "1" ]; then rm -f "$src"; echo "cutover: removed $src"; fi
}
# rank 5 — LiteLLM ADMIN key (source path is operator-supplied: the repo never
# hardcodes it; FOREMAN_LITELLM_ADMIN_KEY_FILE is the same env
# node_render_gateway.py:46 uses). The per-ticket keys the daemon mints replace
# the long-lived worker key at fworker.py's build_env.
if [ -n "${FOREMAN_LITELLM_ADMIN_KEY_FILE:-}" ]; then
    migrate "$FOREMAN_LITELLM_ADMIN_KEY_FILE" litellm-admin.token
fi
# rank 8 — eBay client credential env (bin/ebay-token:6). The daemon mints its
# own access tokens from this; the lumen-readable token cache is not migrated
# (it holds only ~2 h access tokens that expire on their own).
migrate /home/lumen/secrets/ebay.env ebay.env
# rank 7 — Cloudflare ADMIN API token for api-call (operator-supplied source;
# the cloudflared TUNNEL credential stays root-owned — instinct #5 unchanged).
if [ -n "${CLOUDFLARE_API_TOKEN_FILE:-}" ]; then
    migrate "$CLOUDFLARE_API_TOKEN_FILE" cloudflare-api.token
fi
# rank 3 — the fleet ssh key fleet-exec's run_ssh used from uid lumen
# (fleet-exec:49). ssh-exec runs the transport in the daemon; cutover here
# additionally requires fleet-exec's run_ssh to be pointed at the broker first.
install -d -m 0700 -o tokeneng -g tokeneng "$STATE/secrets/fleet-ssh"
migrate "${FLEET_EXEC_SSH_KEY:-/home/lumen/.ssh/id_ed25519}" fleet-ssh/id_ed25519
migrate "${FLEET_EXEC_SSH_KEY:-/home/lumen/.ssh/id_ed25519}.pub" fleet-ssh/id_ed25519.pub

# T10 verbs read grant body-files from worker worktrees and the policy yaml
# from the harness tree: traversal only, no read widening beyond world-readable.
setfacl -m u:tokeneng:x /home/lumen/worktrees 2>/dev/null || true

# Code runs from a root-owned copy: uid lumen must not be able to edit what uid
# tokeneng executes, or the file-permission boundary is fiction. The policy
# module is snapshotted from bin for the same reason.
install -D -m 0755 -o root -g root "$SRC/token_engine_daemon.py" "$LIB/token_engine_daemon.py"
install -D -m 0644 -o root -g root "$POLICY_SRC" "$LIB/fleet_exec_policy.py"

# Dual audit sink (fleet-exec:112-114): tokeneng needs traversal into
# /home/lumen/foreman/events plus append on current and future *.jsonl there.
# ACLs, not group surgery — tokeneng gets exactly these paths and nothing else.
setfacl -m u:tokeneng:x /home/lumen /home/lumen/foreman
setfacl -m u:tokeneng:rwx "$EVENTS"
# rwX not rw: an inherited named-user entry SHADOWS other::r-x, so without X a
# subdirectory created under events/ would be untraversable by tokeneng even
# when world-executable (bit us on the wq#2270 gate replay).
setfacl -d -m u:tokeneng:rwX "$EVENTS"
find "$EVENTS" -maxdepth 1 -name '*.jsonl' -exec setfacl -m u:tokeneng:rw {} +

# (f) Hardened unit.
install -D -m 0644 "$SRC/systemd/token-engine.service" /etc/systemd/system/token-engine.service
systemctl daemon-reload
systemctl enable token-engine.service
systemctl restart token-engine.service

# Gate replay, from root so it can impersonate the worker uid.
sleep 1
systemctl is-active token-engine.service
if runuser -u lumen -- cat "$STATE/secrets/.canary" 2>/tmp/te-eacces.err; then
    echo "FAIL: uid lumen read the canary — store is open" >&2
    exit 1
fi
grep -q "Permission denied" /tmp/te-eacces.err
echo "OK: uid lumen -> EACCES on $STATE/secrets/.canary"
ls -l /run/token-engine/broker.sock
echo "token-engine installed and active"
