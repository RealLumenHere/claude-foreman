#!/bin/bash
# wq#5014 - install the commit-check pre-receive hook on the Anubis Gitea. FAIL-CLOSED hook.
# Runs ON ANUBIS as root (see README.md for the deploy one-liner, which ships the wheel).
# Idempotent. Touches only: /usr/local/lib/lumen-commit-check/ (hook.py + unzipped wheel),
# /etc/lumen-commit-check/cchk.toml, and one file per repo, hooks/pre-receive.d/60-commit-check.
# Gitea's own hooks are not edited. No service is restarted.
#   install.sh             install / refresh library, policy and every repo shim
#   install.sh --lib-only  install / refresh library + policy, no shims (dry-run stage)
#   install.sh --verify    wq#1696 deploy-verify contract, read-only
#   install.sh --check     list repos with no shim, change nothing
#   install.sh --remove    rollback: delete the shims (library and policy stay)
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="${GITEA_REPO_ROOT:-/var/lib/gitea/data/repositories}"
LIB=/usr/local/lib/lumen-commit-check
ETC=/etc/lumen-commit-check
SHIM="hooks/pre-receive.d/60-commit-check"
WHEEL=commit_check-2.22.0-py3-none-any.whl
WHEEL_SHA256=0f50e5c065a19b5d2d1b3672e744a0aea298922b3facbe92fbc0660f64bc7b9d

repos() { find "$ROOT" -mindepth 2 -maxdepth 2 -type d -name '*.git' | sort; }
owner() { stat -c %U "$ROOT"; }

case "${1:-}" in
  --verify)
    . "$SRC/../_verify.sh"
    if [ "$(hostname -s)" = anubis ]; then
      vfile "$SRC/hook.py" "$LIB/hook.py"
      vfile "$SRC/cchk.toml" "$ETC/cchk.toml"
      vfile "$SRC/exempt-repos" "$ETC/exempt-repos"
      [ -f "$LIB/site/commit_check/main.py" ] || _vmiss "absent: $LIB/site/commit_check"
      for r in $(repos); do
        [ -x "$r/$SHIM" ] || _vmiss "no pre-receive shim: $r"
      done
    else
      vremote_up anubis || vdone
      vremote_file anubis "$SRC/hook.py" "$LIB/hook.py"
      vremote_file anubis "$SRC/cchk.toml" "$ETC/cchk.toml"
      vremote_file anubis "$SRC/exempt-repos" "$ETC/exempt-repos"
      unhooked="$($_VSSH anubis \
        "sudo -n find $ROOT -mindepth 2 -maxdepth 2 -type d -name '*.git' \
         '!' -exec test -x '{}/$SHIM' ';' -print | wc -l" 2>/dev/null || echo ERR)"
      case "$unhooked" in
        0)   ;;
        ERR|"") echo "ERR anubis: could not enumerate repos"; _V_ERR=1 ;;
        *)   _vmiss "anubis: $unhooked repo(s) without the commit-check shim" ;;
      esac
    fi
    vdone ;;
  --check)
    n=0; for r in $(repos); do [ -x "$r/$SHIM" ] || { echo "missing: $r"; n=$((n+1)); }; done
    echo "$n repo(s) without the hook"; exit $((n>0)) ;;
  --remove)
    for r in $(repos); do rm -f "$r/$SHIM" && echo "removed: $r"; done; exit 0 ;;
esac

[ -f "$SRC/$WHEEL" ] || { echo "missing $SRC/$WHEEL (README: deploy ships it)"; exit 2; }
echo "$WHEEL_SHA256  $SRC/$WHEEL" | sha256sum -c --quiet - || { echo "wheel sha256 mismatch"; exit 2; }
python3 -m py_compile "$SRC/hook.py"
python3 -I -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$SRC/cchk.toml"

install -d -m 755 "$LIB" "$ETC"
rm -rf "$LIB/site.new"
python3 -I -m zipfile -e "$SRC/$WHEEL" "$LIB/site.new"
chmod -R go-w,a+rX "$LIB/site.new"
rm -rf "$LIB/site.old"; [ -d "$LIB/site" ] && mv "$LIB/site" "$LIB/site.old"
mv "$LIB/site.new" "$LIB/site"; rm -rf "$LIB/site.old"
install -m 755 -o root -g root "$SRC/hook.py" "$LIB/hook.py"
install -m 644 -o root -g root "$SRC/cchk.toml" "$ETC/cchk.toml"
install -m 644 -o root -g root "$SRC/exempt-repos" "$ETC/exempt-repos"
# smoke: the checker runs and loads the policy (rc 0 = message passes)
printf 'fix(x): smoke\n' > "$LIB/.smoke"
python3 -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from commit_check.main import main; sys.exit(main())' \
  "$LIB/site" -c "$ETC/cchk.toml" --message --no-banner --compact "$LIB/.smoke"
rm -f "$LIB/.smoke"
echo "library + policy installed: $LIB $ETC"
[ "${1:-}" = --lib-only ] && exit 0

OWNER="$(owner)"
for r in $(repos); do
  install -d -o "$OWNER" -g "$OWNER" -m 755 "$r/hooks/pre-receive.d"
  printf '#!/bin/sh\n# wq#5014: fleet commit policy (fail-closed). Break glass: touch %s/DISABLED.\nexec /usr/bin/python3 -I %s/hook.py\n' "$ETC" "$LIB" > "$r/$SHIM.new"
  chown "$OWNER:$OWNER" "$r/$SHIM.new"; chmod 755 "$r/$SHIM.new"
  mv -f "$r/$SHIM.new" "$r/$SHIM"
  echo "hook: $r"
done
"$0" --check
