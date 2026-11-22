# ops/wq5247-failed-units/remote-bes.sh — runs ON bes as `bash -s -- DRY TS`, with
# lib.sh prepended by install.sh (cat lib.sh remote-bes.sh | ssh ... bash -s).
# Exit 0 = unit not failed afterwards (or would not be, under DRY=1); 1 = unfixed.
DRY="${1:-1}"
TS="${2:-$(date -u +%Y%m%d_%H%M%S)}"
U=grub-initrd-fallback.service
G="${WQ5247_GRUBENV:-/boot/grub/grubenv}"
SUDO="sudo -n"
D="${WQ5247_DROPIN_DIR:-/etc/systemd/system/$U.d}"
F="$D/wq5337-lp2156423.conf"

# wq#5337: read grubenv as root when sudo -n works (the unit itself runs as root);
# otherwise read as this user and let grubenv_state report `unreadable`, never a
# permission error dressed up as a bad signature.
R=""
if $SUDO true 2>/dev/null; then R="$SUDO"; fi

echo "== bes: $U"
systemctl status "$U" --no-pager 2>&1 | sed -n '1,8p'
echo '-- journal (this boot)'
{ $SUDO journalctl -b -u "$U" --no-pager 2>/dev/null || journalctl -b -u "$U" --no-pager 2>&1; } | tail -n 15
echo "-- grubenv (read as: ${R:-$(id -un) (sudo -n refused)})"; ls -l "$G" 2>&1; grep -E ' /boot(/efi)? ' /proc/mounts
s="$(grubenv_state "$G" $R)"
echo "  grubenv_state: $s"
list="$($R grub-editenv "$G" list 2>&1)"; lrc=$?
echo "  grub-editenv list rc=$lrc: $list"
order="$(systemctl cat "$U" --no-pager 2>/dev/null | initrd_fallback_order)"
race=0
if [ "$order" = stale ] && systemctl cat grub2-common.service --no-pager >/dev/null 2>&1; then race=1; fi
echo "  unit ordering: $order (LP#2156423 race possible: $race)"

# race_fix — wq#5337: unit ordered after the pre-rename grub-common.service while
# grub2-common.service exists = Ubuntu LP#2156423, a boot race that recurs every boot
# with grubenv valid on disk. Apply the packaged fix's After= as a drop-in; the real
# fix is the grub2 upgrade (Ubuntu 26.04.1 changelog). No-op when race=0.
race_fix() {
  [ "$race" = 1 ] || return 0
  echo "  cause: LP#2156423 — $U orders After=grub-common.service, but that unit is now grub2-common.service; both rewrite grubenv in parallel at boot"
  echo "  follow-up: sudo apt-get install --only-upgrade grub2-common  (then the drop-in $F is redundant)"
  if [ "$DRY" = 1 ]; then
    echo "  would: install drop-in $F (After=grub2-common.service); daemon-reload"; return 0
  fi
  local tmp
  tmp="$(mktemp)"; initrd_fallback_dropin >"$tmp"
  $SUDO install -d -m 0755 "$D" && $SUDO install -m 0644 "$tmp" "$F" || { echo 'ERR bes: sudo -n refused'; rm -f "$tmp"; return 1; }
  rm -f "$tmp"
  $SUDO systemctl daemon-reload
  echo "  installed $F"
}

if [ "$(systemctl is-failed "$U" 2>/dev/null)" != failed ]; then
  echo "VERDICT bes: $U not failed now — nothing to restart"
  race_fix || exit 1
  exit 0
fi
case "$s|$list" in
  'unreadable|'*)
    echo "UNFIXED bes: cannot read $G (sudo -n refused?) — not treating an unreadable file as corrupt"
    exit 1 ;;
  'bad-header|'*|*'invalid environment block'*) ;;
  ok\|*)
    # wq#5332: the live run found exactly this. Journal: "invalid environment block" at
    # 2026-11-12 23:27:49 (boot); grubenv mtime is that same minute and it is valid now
    # (signature ok, `grub-editenv list` rc=0, / rw at 7%). It was rewritten during boot
    # after the unit had read it, so the failure was boot-time only (same shape as
    # lumen-audit.sh's "reload succeeds" branch). Re-run the unit's own two `unset`
    # calls; never touch the file. Still failing -> UNFIXED with the journal.
    if [ "$lrc" -ne 0 ]; then
      echo "UNFIXED bes: grubenv signature ok but grub-editenv list rc=$lrc (output above)"; exit 1
    fi
    echo 'VERDICT bes: grubenv valid now — boot-time failure only; re-run the unit'
    race_fix || exit 1
    if [ "$DRY" = 1 ]; then echo "  would: reset-failed + restart $U"; exit 0; fi
    $SUDO systemctl reset-failed "$U" || { echo 'ERR bes: sudo -n refused'; exit 1; }
    $SUDO systemctl restart "$U"
    st="$(systemctl is-failed "$U" 2>/dev/null)"
    echo "  $U: $st"
    if [ "$st" = failed ]; then
      $SUDO journalctl -b -u "$U" --no-pager 2>/dev/null | tail -n 6
      echo "UNFIXED bes: $U still fails on a valid grubenv (journal above)"; exit 1
    fi
    exit 0 ;;
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
