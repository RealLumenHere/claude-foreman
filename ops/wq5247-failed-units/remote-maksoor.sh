# ops/wq5247-failed-units/remote-maksoor.sh — runs ON maksoor as `bash -s -- DRY`,
# with lib.sh prepended by install.sh (cat lib.sh remote-maksoor.sh | ssh ... bash -s).
# Exit 0 = unit not failed afterwards (or would not be, under DRY=1); 1 = unfixed.
DRY="${1:-1}"
U=max-brightness.service
D="${WQ5247_DROPIN_DIR:-/etc/systemd/system/$U.d}"
F="$D/wq5247-require-backlight.conf"
SUDO="sudo -n"

echo "== maksoor: $U"
systemctl status "$U" --no-pager 2>&1 | sed -n '1,8p'
echo '-- unit'; systemctl cat "$U" --no-pager 2>&1
echo '-- journal (this boot)'
{ $SUDO journalctl -b -u "$U" --no-pager 2>/dev/null || journalctl -b -u "$U" --no-pager 2>&1; } | tail -n 15
echo '-- /sys/class/backlight'; ls -l /sys/class/backlight 2>&1

mapfile -t T < <(systemctl cat "$U" --no-pager 2>/dev/null | backlight_targets)
missing=0
if [ "${#T[@]}" -eq 0 ]; then
  compgen -G '/sys/class/backlight/*' >/dev/null || { echo '  absent: any /sys/class/backlight/* device'; missing=1; }
else
  for t in "${T[@]}"; do
    compgen -G "$t" >/dev/null || { echo "  absent: $t"; missing=1; }
  done
fi

if [ "$(systemctl is-failed "$U" 2>/dev/null)" != failed ]; then
  echo "VERDICT maksoor: $U not failed now — nothing to do"; exit 0
fi
if [ "$missing" -eq 0 ]; then
  echo 'UNFIXED maksoor: the backlight device the unit writes exists, so the failure has another cause — journal above'
  exit 1
fi

echo 'VERDICT maksoor: the backlight device the unit writes does not exist on this board — add a skip condition'
tmp="$(mktemp)"
backlight_dropin "${T[@]}" >"$tmp"
cat "$tmp"
if [ "$DRY" = 1 ]; then
  echo "  would install $F, daemon-reload, reset-failed, restart $U"
  rm -f "$tmp"; exit 0
fi
$SUDO install -d -m 0755 "$D" && $SUDO install -m 0644 "$tmp" "$F" || { echo 'ERR maksoor: sudo -n refused'; rm -f "$tmp"; exit 1; }
rm -f "$tmp"
$SUDO systemctl daemon-reload
$SUDO systemctl reset-failed "$U"
$SUDO systemctl restart "$U"
st="$(systemctl is-failed "$U" 2>/dev/null)"
echo "  $U: $st"
[ "$st" != failed ]
