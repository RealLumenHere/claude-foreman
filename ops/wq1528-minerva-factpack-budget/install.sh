#!/usr/bin/env bash
# wq#1528 — install the factpack facts-budget fix (design §2c/§2e).
# Run as root on the Minerva host, detached:
#   /home/lumen/bin/lumen-run start --root --name wq1528-factpack -- \
#       /home/lumen/foreman/ops/wq1528-minerva-factpack-budget/install.sh
#
# Ships: minerva_factpack.py (FACTS_RESERVE + CAP 22500 + build_context floor),
#        minerva_verify.py  (JUDGE_CTX_CHARS 23000 + rung-window bound + the
#                            wq#1383 assertion extended to the factpack reserve),
#        zz-wq1528-judge-window.conf (service env, overrides the stale
#                            factpack.conf JUDGE_CTX_CHARS=16000).
#
# Hash-gated: each live file must match the mirror pre-image this change was
# built against (ops built from the sweep-gated /home/lumen/research/minerva-ref
# copy, ISSUE-LOG #233). A mismatch means live drifted since the hourly sync —
# abort untouched, re-sync the mirror, rebuild. Idempotent: already-installed
# files are skipped. Never restarts the service; env + code take effect on the
# next timer start.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva
BACKUP_DIR=/home/lumen/backups/wq1528
DEST_DIR=/etc/systemd/system/minerva-proposal.service.d

declare -A PRE=(
  [minerva_factpack.py]=bae99c75326fd9ca2d43fb641faa8307c9c6b11ca58f9b093c214d5fadf3d525
  [minerva_verify.py]=f34011293af3174e813c0ae11f0155c6f5c55977f83b8d3987cb21a38731d351
)

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

for f in minerva_factpack.py minerva_verify.py; do
    if cmp -s "$HERE/$f" "$LIVE/$f"; then
        echo "unchanged: $LIVE/$f"
        continue
    fi
    got="$(sha256sum "$LIVE/$f" | cut -d' ' -f1)"
    if [ "$got" != "${PRE[$f]}" ]; then
        echo "ABORT: $LIVE/$f pre-image mismatch (live $got, expected ${PRE[$f]})." >&2
        echo "Live drifted since the mirror sync this patch was built from; rebuild." >&2
        exit 2
    fi
    cp -p "$LIVE/$f" "$BACKUP_DIR/$stamp.$f.bak"
    python3 -c "import ast; ast.parse(open('$HERE/$f').read())"
    install -m 0644 -o lumen -g lumen "$HERE/$f" "$LIVE/$f"
    echo "installed: $LIVE/$f (backup: $BACKUP_DIR/$stamp.$f.bak)"
done

CONF="$DEST_DIR/zz-wq1528-judge-window.conf"
if [ -f "$CONF" ] && cmp -s "$HERE/zz-wq1528-judge-window.conf" "$CONF"; then
    echo "unchanged: $CONF"
else
    [ -f "$CONF" ] && cp -p "$CONF" "$BACKUP_DIR/$stamp.zz-wq1528-judge-window.conf.bak"
    install -m 0644 "$HERE/zz-wq1528-judge-window.conf" "$CONF"
    systemctl daemon-reload
    echo "installed: $CONF (daemon-reload done)"
fi

# Effective sizes after the drop-in chain (zz- wins over factpack.conf).
systemctl show -p Environment minerva-proposal.service \
    | grep -o -e 'VERIFY_JUDGE_CTX_CHARS=[^ ]*' -e 'JUDGE_CTX_CHARS=[^ ]*' \
              -e 'FACTPACK_CAP=[^ ]*' -e 'FACTPACK_FACTS_RESERVE=[^ ]*' || true
