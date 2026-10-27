#!/usr/bin/env bash
# wq#4706 - install the gate-snapshot-prune timer: the caller wq#4654 shipped without.
# Run as root via `lumen-run` (repo convention). Idempotent.
# The code lives in claude-foreman (harness/action_gate.py prune_vault); this bundle is the units.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/gate-snapshot-prune.service" /etc/systemd/system/gate-snapshot-prune.service
  vunit_file "$SRC/gate-snapshot-prune.timer"   /etc/systemd/system/gate-snapshot-prune.timer
  venabled   gate-snapshot-prune.timer
  vactive    gate-snapshot-prune.timer
  # wq#4706: a vfile+vactive pair proves the unit is installed, not that the thing it runs
  # still works — the defect this bundle fixes was code with no caller, and a unit whose
  # ExecStart has rotted away under it is the same shape of lie (wq#3228). So ask the
  # callee what it implements, then run it read-only against the REAL vault.
  vcontains /home/lumen/foreman/harness/action_gate.py 'def prune_vault\(.*dry_run'
  # VAULT is named explicitly, not left to expanduser("~"): --verify runs as root about as
  # often as it runs as lumen, and under root the default resolves to /root/.secrets, which
  # is absent, so prune_vault would return [] and the probe could not fail.
  VAULT=/home/lumen/.secrets/gate-snapshots
  if ! FOREMAN_GATE_SNAP_VAULT="$VAULT" /usr/bin/python3 \
       /home/lumen/foreman/harness/action_gate.py prune-vault --dry-run \
       | grep -q "would prune .* file(s) from $VAULT"; then
    _vmiss "prune-vault --dry-run does not run against $VAULT on the primary tree"
  fi
  vdone
fi

[ -f /home/lumen/foreman/harness/action_gate.py ] || { echo "missing /home/lumen/foreman/harness/action_gate.py (land claude-foreman first)"; exit 1; }
grep -q "def prune_vault" /home/lumen/foreman/harness/action_gate.py || { echo "action_gate.py on the primary tree has no prune_vault (PR #2473 not landed/ff'd)"; exit 1; }
echo "== read-only check before enabling =="
sudo -u lumen env FOREMAN_GATE_SNAP_VAULT=/home/lumen/.secrets/gate-snapshots \
  /usr/bin/python3 /home/lumen/foreman/harness/action_gate.py prune-vault --dry-run | tail -1
echo "== systemd units =="
install -m 644 "$SRC/gate-snapshot-prune.service" /etc/systemd/system/gate-snapshot-prune.service
install -m 644 "$SRC/gate-snapshot-prune.timer"   /etc/systemd/system/gate-snapshot-prune.timer
systemctl daemon-reload
systemctl enable --now gate-snapshot-prune.timer
systemctl list-timers gate-snapshot-prune.timer --no-pager || true
echo "installed. Watch: journalctl -u gate-snapshot-prune -n 20"
