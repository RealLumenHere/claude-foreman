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
install -D -m 0644 -o root -g root "$SRC/providers.json" "$LIB/providers.json"

# wq#2271 (T1, design §3.1): the git credential helper. Root-owned in PATH so
# `credential.helper = token-engine` resolves for every uid; the per-worktree
# config that selects it is written by fworker.make_worktree.
install -D -m 0755 -o root -g root "$SRC/git-credential-token-engine" \
    /usr/local/bin/git-credential-token-engine

# wq#2272 (T3, design §3.3): the fleet pre-push wall. Root-owned dir + hook —
# uid lumen must not be able to edit or replace what core.hooksPath selects
# (guard.sh's primary-tree freeze is the model: the wall lives outside what a
# worker can write). fworker points each worker worktree at it; the global
# flip for every other clone happens at TE_FINALIZE_MOVE below, together with
# retiring the store helper the hook exists to catch.
install -d -m 0755 -o root -g root "$LIB/hooks"
install -D -m 0755 -o root -g root "$SRC/hooks/pre-push" "$LIB/hooks/pre-push"

# wq#2278 (T10, design §7 ranks 5-8 + rank 3): migrate the remaining
# inventory into the store. COPY, not move — the same T4 rule as the forge
# tokens above: plug-ctl/power-remediate still source tapo.env, bin/ebay-token
# still reads ebay.env, and fleet-exec still runs ssh itself until its
# bin-repo change lands ssh-run delegation; deleting an original here would
# break a live consumer (power remediation!) before its verb path exists.
# Once every consumer is on verbs, re-run with TE_FINALIZE_MOVE=1 to shred the
# originals — THAT is the step that makes ranks 5-8 unreadable by uid lumen.
migrate() { # migrate <src> <dest-basename>
    local src="$1" dest="$STATE/secrets/$2"
    [ -f "$src" ] || { echo "skip: $src absent"; return 0; }
    install -m 0600 -o tokeneng -g tokeneng "$src" "$dest"
    if [ "${TE_FINALIZE_MOVE:-0}" = 1 ]; then
        shred -u "$src" 2>/dev/null || rm -f "$src"
        echo "moved $src -> $dest (original shredded)"
    else
        echo "copied $src -> $dest (original stays until its reader migrates)"
    fi
}
# wq#2273 (T4) copied the forge tokens in; wq#2271 (T1) turns them into
# migrate() rows so TE_FINALIZE_MOVE=1 — once every clone pushes through the
# helper — shreds the /home/lumen/.secrets originals (ranks 1/2/4).
migrate /home/lumen/.secrets/github-frank.token         github-frank.token
migrate /home/lumen/.secrets/github-lumen-classic.token github-lumen-classic.token
migrate /home/lumen/.secrets/gitea-lumen.token          gitea-lumen.token
migrate /home/lumen/secrets/ebay.env        ebay.env               # rank 8
migrate /home/lumen/.config/tapo.env        tapo.env
migrate "${CLOUDFLARE_TOKEN_SRC:-/home/lumen/.secrets/cloudflare-api.token}" \
                                            cloudflare-api.token   # rank 7 (API token; tunnel cred stays root-only)
migrate /home/lumen/.ssh/id_ed25519         fleet-ssh.key          # rank 3
# Rank 5: the LiteLLM mint key is operator-held (never in a lumen-readable
# file — ops/wq1747 credential hygiene), so it arrives via env at install
# time: LITELLM_ADMIN_KEY=... lumen-run start --root -- bash install.sh
if [ -n "${LITELLM_ADMIN_KEY:-}" ]; then
    install -m 0600 -o tokeneng -g tokeneng /dev/null "$STATE/secrets/litellm-admin.token"
    printf '%s\n' "$LITELLM_ADMIN_KEY" > "$STATE/secrets/litellm-admin.token"
    echo "installed litellm-admin.token from env"
else
    echo "skip: LITELLM_ADMIN_KEY not set — gateway-key mint stays unavailable" \
         "(fworker falls back to the long-lived virtual key)"
fi
# ssh-run runs ssh as uid tokeneng: it needs a writable ~/.ssh for known_hosts.
install -d -m 0700 -o tokeneng -g tokeneng "$STATE/.ssh"

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

# wq#2271 (T1, design §3.2): token-free remotes. One immediate --fix pass
# strips any userinfo/token from every clone's remote URLs (path-only output),
# and the wq#2243 hourly detect+fix timer — never installed, ISSUE-LOG 540 —
# goes live as the standing sweep.
SWEEP_DIR="$(dirname "$SRC")/ops/wq2243-remote-credential-sweep"
if [ -d "$SWEEP_DIR" ]; then
    runuser -u lumen -- python3 "$SWEEP_DIR/remote_credential_sweep.py" \
        scan --fix || true   # exit 1 = unstrippable finding; paged, not fatal
    bash "$SWEEP_DIR/install.sh"
else
    echo "skip: $SWEEP_DIR absent — sweep --fix/timer not run" >&2
fi

# T1 finalize: with every worktree on the helper and the sweep clean, the
# store-helper surface itself goes away — the global credential.helper=store
# line and ~/.git-credentials are exactly the on-disk token copies the design
# retires. Only under TE_FINALIZE_MOVE=1, same contract as the token moves.
if [ "${TE_FINALIZE_MOVE:-0}" = 1 ]; then
    # wq#2272 (T3): once the store helper is gone, every clone of uid lumen —
    # primaries included — goes behind the pre-push wall. Not before: until
    # finalize, primaries legitimately push through the global helper the hook
    # would refuse.
    runuser -u lumen -- git config --global core.hooksPath "$LIB/hooks"
    runuser -u lumen -- git config --global --unset-all credential.helper || true
    if [ -f /home/lumen/.git-credentials ]; then
        shred -u /home/lumen/.git-credentials 2>/dev/null || rm -f /home/lumen/.git-credentials
        echo "finalize: ~/.git-credentials shredded, global store helper removed"
    fi
fi

echo "token-engine installed and active"
