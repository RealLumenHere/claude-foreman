#!/usr/bin/env bash
# wq#2430 — install provenance-tagged evidence + stale-TTL gate into the live
# Minerva verify path. Run detached via lumen-run (lumen-change-safety):
#   /home/lumen/bin/lumen-run start --root --name wq2430-provenance -- \
#       /home/lumen/foreman/ops/wq2430-minerva-evidence-provenance/install.sh
# Steps: verify pre-image -> precheck staged -> backup -> apply -> health.
# Rollback: install.sh rollback   (restores the .bak pair from BK)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva
MIRROR=/home/lumen/research/minerva-ref
BK=/home/lumen/backups/wq2430
FILES="minerva_factpack.py minerva_verify.py"

if [ "${1:-}" = "rollback" ]; then
    for f in $FILES; do cp -v "$BK/$f.bak" "$LIVE/$f"; done
    echo "wq2430 ROLLBACK OK"; exit 0
fi

if grep -q 'wq#2430' "$LIVE/minerva_verify.py"; then
    echo "wq2430 already installed — nothing to do"; exit 0
fi

# --- 1. pre-image gate: live must be the exact revision this change was built
# on (line-count pin + anchor lines + mirror agreement). Abort on any drift.
[ "$(grep -c ^ "$LIVE/minerva_factpack.py")" = "332" ] || { echo "PREIMAGE FAIL: factpack line count"; exit 1; }
[ "$(grep -c ^ "$LIVE/minerva_verify.py")" = "611" ]   || { echo "PREIMAGE FAIL: verify line count"; exit 1; }
grep -qF 'def retrieve(answer_text, k=TOP_K):' "$LIVE/minerva_factpack.py" || { echo "PREIMAGE FAIL: factpack anchor"; exit 1; }
grep -qF "def verify(answer: str, context: str, subject: str = 'unknown', model: str = 'unknown', mode: str = 'strict') -> dict:" \
    "$LIVE/minerva_verify.py" || { echo "PREIMAGE FAIL: verify signature anchor"; exit 1; }
grep -qF "CACHE.write_text(json.dumps(lines))" "$LIVE/minerva_factpack.py" || { echo "PREIMAGE FAIL: cache-v1 anchor"; exit 1; }
for f in $FILES; do
    if ! cmp -s "$LIVE/$f" "$MIRROR/$f"; then
        echo "PREIMAGE FAIL: live $f differs from mirror (sync lag or unrecorded live edit)"; exit 1
    fi
done

# --- 2. precheck the staged copies: compile, bounded diff, offline tests.
python3 -m py_compile "$HERE/minerva_factpack.py" "$HERE/minerva_verify.py"
for f in $FILES; do
    del=$(diff "$LIVE/$f" "$HERE/$f" | grep -c '^<' || true)
    [ "$del" -le 15 ] || { echo "PRECHECK FAIL: $f replaces $del pre-existing lines (>15) — staged copy suspect"; exit 1; }
done
(cd "$HERE" && python3 test_wq2430_provenance.py) || { echo "PRECHECK FAIL: test suite"; exit 1; }

# --- 3. backup.
mkdir -p "$BK"
for f in $FILES; do cp -v "$LIVE/$f" "$BK/$f.bak"; done
sha256sum "$BK"/*.bak "$HERE"/minerva_factpack.py "$HERE"/minerva_verify.py > "$BK/manifest.sha256"

# --- 4. apply.
for f in $FILES; do cp -v "$HERE/$f" "$LIVE/$f"; done

# --- 5. health: modules import from the live tree, new contract present,
# provenance + gate smoke (health_check.py).
python3 "$HERE/health_check.py" "$LIVE"
rm -f /tmp/minerva_factpack_cache.json   # drop v1 cache so v2 format takes over cleanly
echo "wq2430 INSTALL OK $(date -u +%FT%TZ)"
