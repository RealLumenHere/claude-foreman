#!/usr/bin/env bash
# wq#2460 — install context-authority validation into the live Minerva verify
# path. Run detached via lumen-run (lumen-change-safety):
#   /home/lumen/bin/lumen-run start --root --name wq2460-authority -- \
#       /home/lumen/foreman/ops/wq2460-minerva-context-authority/install.sh
# Steps: verify pre-image -> precheck staged -> backup -> apply -> health.
# Rollback: install.sh rollback   (restores the .bak from BK)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva
MIRROR=/home/lumen/research/minerva-ref
BK=/home/lumen/backups/wq2460
F=minerva_verify.py

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# install.sh:20 already treats a 'wq#2460' marker in the live file as "installed";
# this reuses that same signal rather than inventing a second, weaker one.
# Byte-equality is wrong here: minerva_verify.py is edited in place by later
# tickets, so an equal file would mean nothing had landed since.
if [ "${1:-}" = --verify ]; then
    . "$HERE/../_verify.sh"
    vcontains "$LIVE/$F" 'wq#2460'
    vcontains "$LIVE/$F" 'AUTHORITATIVE|STALE|UNVERIFIED'
    vdone
fi

if [ "${1:-}" = "rollback" ]; then
    cp -v "$BK/$F.bak" "$LIVE/$F"
    echo "wq2460 ROLLBACK OK"; exit 0
fi

if grep -q 'wq#2460' "$LIVE/$F"; then
    echo "wq2460 already installed — nothing to do"; exit 0
fi

# --- 1. pre-image gate: live must be the exact wq#2430 revision this change
# was built on (line-count pin + anchor lines + mirror agreement).
[ "$(grep -c ^ "$LIVE/$F")" = "715" ] || { echo "PREIMAGE FAIL: verify line count"; exit 1; }
grep -qF "def verify(answer: str, context: str, subject: str = 'unknown', model: str = 'unknown', mode: str = 'strict', evidence: list = None) -> dict:" \
    "$LIVE/$F" || { echo "PREIMAGE FAIL: verify signature anchor"; exit 1; }
grep -qF 'def stale_support_claims(claims: list, evidence) -> tuple:' "$LIVE/$F" \
    || { echo "PREIMAGE FAIL: wq2430 stale-gate anchor"; exit 1; }
if ! cmp -s "$LIVE/$F" "$MIRROR/$F"; then
    echo "PREIMAGE FAIL: live $F differs from mirror (sync lag or unrecorded live edit)"; exit 1
fi

# --- 2. precheck the staged copy: compile, bounded diff, offline tests.
python3 -m py_compile "$HERE/$F"
del=$(diff "$LIVE/$F" "$HERE/$F" | grep -c '^<' || true)
[ "$del" -le 15 ] || { echo "PRECHECK FAIL: $F replaces $del pre-existing lines (>15) — staged copy suspect"; exit 1; }
(cd "$HERE" && python3 test_wq2460_context_authority.py) || { echo "PRECHECK FAIL: test suite"; exit 1; }

# --- 3. backup.
mkdir -p "$BK"
cp -v "$LIVE/$F" "$BK/$F.bak"
sha256sum "$BK/$F.bak" "$HERE/$F" > "$BK/manifest.sha256"

# --- 4. apply.
cp -v "$HERE/$F" "$LIVE/$F"

# --- 5. health: live import, schema-compatible SELECT, classifier smoke.
python3 "$HERE/health_check.py" "$LIVE"
echo "wq2460 INSTALL OK $(date -u +%FT%TZ)"
