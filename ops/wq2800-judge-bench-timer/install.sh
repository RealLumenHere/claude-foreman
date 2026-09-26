#!/bin/sh
# wq#2800 -- installs lumen-judge-bench.{service,timer} for the weekly judge
# drift-bench (155-case golden set vs bench_baseline.json, alerts aq lane
# 'judge' on false_allow_count>0 or verdict_accuracy drop >0.1).
#
# SHIPPED DISABLED ON PURPOSE: this script copies the units and runs
# daemon-reload, but never enables or starts the timer -- Frank arms it
# himself with: sudo systemctl enable --now lumen-judge-bench.timer
# (mirrors lumen-reflector.timer's own staged-but-disabled pattern).
#
# Usage:
#   sudo sh install.sh            # install (idempotent)
#   sudo sh install.sh --verify   # confirm units are present and NOT enabled
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/etc/systemd/system

if [ "${1:-}" = "--verify" ]; then
    ok=1
    for u in lumen-judge-bench.service lumen-judge-bench.timer; do
        if ! cmp -s "$HERE/$u" "$DEST/$u" 2>/dev/null; then
            echo "wq2800 verify: $u missing or differs from repo copy" >&2
            ok=0
        fi
    done
    if systemctl is-enabled lumen-judge-bench.timer >/dev/null 2>&1; then
        echo "wq2800 verify: timer is ENABLED -- expected disabled-by-default until Frank arms it" >&2
        ok=0
    fi
    if [ "$ok" = "1" ]; then
        echo "wq2800 verify: units installed, daemon knows them, timer correctly NOT enabled"
        exit 0
    fi
    exit 1
fi

cp "$HERE/lumen-judge-bench.service" "$DEST/lumen-judge-bench.service"
cp "$HERE/lumen-judge-bench.timer" "$DEST/lumen-judge-bench.timer"
systemctl daemon-reload
echo "wq2800 install: units copied + daemon-reloaded, timer left DISABLED (arm with: systemctl enable --now lumen-judge-bench.timer)"
