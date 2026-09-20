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

# Code runs from a root-owned copy: uid lumen must not be able to edit what uid
# tokeneng executes, or the file-permission boundary is fiction. The policy
# module is snapshotted from bin for the same reason. te_client.py is installed
# too (wq#2273): lumen-pr calls the broker through this stable root-owned path
# rather than a checkout it could edit.
install -D -m 0755 -o root -g root "$SRC/token_engine_daemon.py" "$LIB/token_engine_daemon.py"
install -D -m 0644 -o root -g root "$SRC/token_ledger.py" "$LIB/token_ledger.py"
install -D -m 0644 -o root -g root "$POLICY_SRC" "$LIB/fleet_exec_policy.py"
install -D -m 0755 -o root -g root "$SRC/te_client.py" "$LIB/te_client.py"
install -D -m 0644 -o root -g root "$SRC/forges.json" "$LIB/forges.json"

# wq#2273 (T4, design §7 ranks 1/2/4): COPY the PR-verb tokens into the engine
# store. Copy, not move — git push still authenticates off the old path until
# T1 (wq#2271) lands the credential-helper migration; deleting here would break
# every push fleet-wide. T1 removes the originals.
for tok in github-frank.token github-lumen-classic.token gitea-lumen.token; do
    if [ -f "/home/lumen/.secrets/$tok" ]; then
        install -m 0600 -o tokeneng -g tokeneng \
            "/home/lumen/.secrets/$tok" "$STATE/secrets/$tok"
    fi
done

# The daemon reads the per-repo identity map live (paths only, no secrets);
# grant tokeneng traversal + read so a map edit needs no reinstall.
if [ -f /home/lumen/infra/github/identity-map.conf ]; then
    setfacl -m u:tokeneng:x /home/lumen/infra /home/lumen/infra/github
    setfacl -m u:tokeneng:r /home/lumen/infra/github/identity-map.conf
fi

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
