# ops/wq5247-failed-units/remote-bes.sh — runs ON bes as `bash -s -- DRY TS`, with
# lib.sh prepended by install.sh (cat lib.sh remote-bes.sh | ssh ... bash -s).
# Exit 0 = unit not failed afterwards (or would not be, under DRY=1); 1 = unfixed.
DRY="${1:-1}"
TS="${2:-$(date -u +%Y%m%d_%H%M%S)}"
U=grub-initrd-fallback.service
G="${WQ5247_GRUBENV:-/boot/grub/grubenv}"
SUDO="sudo -n"

echo "== bes: $U"
systemctl status "$U" --no-pager 2>&1 | sed -n '1,8p'
echo '-- journal (this boot)'
{ $SUDO journalctl -b -u "$U" --no-pager 2>/dev/null || journalctl -b -u "$U" --no-pager 2>&1; } | tail -n 15
echo '-- grubenv'; ls -l "$G" 2>&1; grep -E ' /boot(/efi)? ' /proc/mounts
s="$(grubenv_state "$G")"
echo "  grubenv_state: $s"
list="$(grub-editenv "$G" list 2>&1)"; lrc=$?
echo "  grub-editenv list rc=$lrc: $list"

if [ "$(systemctl is-failed "$U" 2>/dev/null)" != failed ]; then
  echo "VERDICT bes: $U not failed now — nothing to do"; exit 0
fi
case "$s|$list" in
  'bad-header|'*|*'invalid environment block'*) ;;
  *) echo 'UNFIXED bes: grubenv is not corrupt, so the failure has another cause (journal above: /boot read-only? full?)'
     exit 1 ;;
esac

echo 'VERDICT bes: grubenv corrupt (same class as Thor 2026-08-08) — back up and recreate'
if [ "$DRY" = 1 ]; then
  echo "  would: cp -a $G $G.corrupt.bak.$TS; grub-editenv $G create; reset-failed + restart $U"
  exit 0
fi
$SUDO cp -a "$G" "$G.corrupt.bak.$TS" || { echo 'ERR bes: sudo -n refused'; exit 1; }
$SUDO grub-editenv "$G" create
$SUDO systemctl reset-failed "$U"
$SUDO systemctl restart "$U"
st="$(systemctl is-failed "$U" 2>/dev/null)"
echo "  $U: $st"
[ "$st" != failed ]
