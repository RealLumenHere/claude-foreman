#!/bin/bash
# wq#2286 - install the secret-scan pre-receive hook on the Anubis Gitea.
# Runs ON ANUBIS as root:  tar c -C ops/wq2286-gitea-secret-scan . | ssh anubis@100.121.17.5 \
#   'rm -rf /tmp/ss && mkdir /tmp/ss && tar x -C /tmp/ss && sudo -n bash /tmp/ss/install.sh'
# Idempotent. Touches only: /usr/local/lib/lumen-secret-scan/, /etc/lumen-secret-scan/allow.txt
# (created once, never overwritten) and one file per repo, hooks/pre-receive.d/50-secret-scan.
# Gitea's own hooks are not edited. No service is restarted.
#   install.sh            install / refresh
#   install.sh --check    list repos that have no shim, change nothing
#   install.sh --remove   rollback: delete the shims (leaves the scanner and allow list)
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="${GITEA_REPO_ROOT:-/var/lib/gitea/data/repositories}"
LIB=/usr/local/lib/lumen-secret-scan
ETC=/etc/lumen-secret-scan
SHIM="hooks/pre-receive.d/50-secret-scan"

repos() { find "$ROOT" -mindepth 2 -maxdepth 2 -type d -name '*.git' | sort; }
# Lazy on purpose: $ROOT only exists on Anubis, and `stat` on a missing path
# kills the script under `set -e` before --verify can run from anywhere else.
owner() { stat -c %U "$ROOT"; }

case "${1:-}" in
  # wq#1696 deploy-verify contract (wq#2985). Read-only, before any install
  # step. This bundle installs on ANUBIS, so a Lumen-local check reports it
  # absent however live it is -- wq#1732 read exactly that and filed it PENDING.
  # Run from Anubis it checks locally; run anywhere else it asks Anubis.
  --verify)
    . "$SRC/../_verify.sh"
    if [ "$(hostname -s)" = anubis ]; then
      vfile "$SRC/scan.py" "$LIB/scan.py"
      [ -e "$ETC/allow.txt" ] || _vmiss "absent: $ETC/allow.txt"
      for r in $(repos); do
        [ -x "$r/$SHIM" ] || _vmiss "no pre-receive shim: $r"
      done
    else
      vremote_up anubis || vdone
      vremote_file anubis "$SRC/scan.py" "$LIB/scan.py"
      [ -n "$($_VSSH anubis "test -e $ETC/allow.txt && echo y" 2>/dev/null)" ] \
        || _vmiss "anubis: absent $ETC/allow.txt"
      unhooked="$($_VSSH anubis \
        "find $ROOT -mindepth 2 -maxdepth 2 -type d -name '*.git' \
         '!' -exec test -x '{}/$SHIM' ';' -print | wc -l" 2>/dev/null || echo ERR)"
      case "$unhooked" in
        0)   ;;
        ERR) echo "ERR anubis: could not enumerate repos"; _V_ERR=1 ;;
        *)   _vmiss "anubis: $unhooked repo(s) without the pre-receive shim" ;;
      esac
    fi
    vdone ;;
  --check)
    n=0; for r in $(repos); do [ -x "$r/$SHIM" ] || { echo "missing: $r"; n=$((n+1)); }; done
    echo "$n repo(s) without the hook"; exit $((n>0)) ;;
  --remove)
    for r in $(repos); do rm -f "$r/$SHIM" && echo "removed: $r"; done; exit 0 ;;
esac

python3 -m py_compile "$SRC/scan.py"
install -d -m 755 "$LIB" "$ETC"
install -m 755 -o root -g root "$SRC/scan.py" "$LIB/scan.py"
[ -e "$ETC/allow.txt" ] || install -m 644 -o root -g root "$SRC/allow.txt" "$ETC/allow.txt"

OWNER="$(owner)"
for r in $(repos); do
  install -d -o "$OWNER" -g "$OWNER" -m 755 "$r/hooks/pre-receive.d"
  printf '#!/bin/sh\n# wq#2286: refuse token-shaped values. Remove this file to disable for this repo.\nexec /usr/bin/python3 %s/scan.py pre-receive\n' "$LIB" > "$r/$SHIM.new"
  chown "$OWNER:$OWNER" "$r/$SHIM.new"; chmod 755 "$r/$SHIM.new"
  mv -f "$r/$SHIM.new" "$r/$SHIM"
  echo "hook: $r"
done
"$0" --check
