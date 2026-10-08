#!/usr/bin/env bash
# Bootstrap the ansible control path on lumen (one-time per control node).
# Usage: ./bootstrap.sh [node]   — with a node arg, also provisions it.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ansible-playbook >/dev/null 2>&1 || {
  echo "installing ansible (user-level, no sudo)"
  python3 -m pip install --user ansible
}

./scripts/gen-inventory.sh || echo "WARN: inventory not refreshed (lumen_state.py unreachable?) — using committed IPs"

ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml --syntax-check

if [ $# -ge 1 ]; then
  exec ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml --limit "$1" -K
fi
echo "bootstrap OK — run: ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml --limit <node> -K"
