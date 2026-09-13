#!/usr/bin/env bash
# wq#1529 — name the silent judge-retry rung and drop it. Run as root on the
# Minerva host, detached:
#   /home/lumen/bin/lumen-run start --root --name wq1529-judge-rung -- \
#       /home/lumen/foreman/ops/wq1529-minerva-judge-rung-name/install.sh
#
# Ships: minerva_verify.py (VERIFY_JUDGE_MODEL_2's code default 'deepseek-v4-flash'
#        -> '', so an unset/missing service drop-in fails closed instead of
#        silently spending on a paid judge rung — see the file's wq#1529 comment).
#
# Hash-gated: the live file must match the mirror pre-image this change was
# built against (ops built from the sweep-gated /home/lumen/research/minerva-ref
# copy, ISSUE-LOG #233). A mismatch means live drifted since the hourly sync —
# abort untouched, re-sync the mirror, rebuild. Idempotent: already-installed
# file is skipped. Never restarts the service; the code takes effect on the
# next timer start.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva
BACKUP_DIR=/home/lumen/backups/wq1529
F=minerva_verify.py
PRE=ba64b83ce001895117e9adf27dc9d9eb9cedeeba65bbcced83088605c9ecfccb

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

if cmp -s "$HERE/$F" "$LIVE/$F"; then
    echo "unchanged: $LIVE/$F"
else
    got="$(sha256sum "$LIVE/$F" | cut -d' ' -f1)"
    if [ "$got" != "$PRE" ]; then
        echo "ABORT: $LIVE/$F pre-image mismatch (live $got, expected $PRE)." >&2
        echo "Live drifted since the mirror sync this patch was built from; rebuild." >&2
        exit 2
    fi
    cp -p "$LIVE/$F" "$BACKUP_DIR/$stamp.$F.bak"
    python3 -c "import ast; ast.parse(open('$HERE/$F').read())"
    install -m 0644 -o lumen -g lumen "$HERE/$F" "$LIVE/$F"
    echo "installed: $LIVE/$F (backup: $BACKUP_DIR/$stamp.$F.bak)"
fi

# Confirm the drop-in itself still carries the explicit empty override
# (belt + suspenders: this install fixes the code default, wq#1501's drop-in
# already sets the env explicitly).
systemctl show -p Environment minerva-proposal.service \
    | grep -o 'VERIFY_JUDGE_MODEL_2=[^ ]*' || true
