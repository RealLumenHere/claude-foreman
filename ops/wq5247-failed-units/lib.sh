# ops/wq5247-failed-units/lib.sh — pure helpers for install.sh (sourced, never run).
# No side effects except audit_disable_rule, which edits only the file it is handed.
# Kept separate so test_failed_units.py can drive every decision against fixtures
# without root, a live audit subsystem, or ssh.

# --- Lumen audit-rules.service ------------------------------------------------
# augenrules --load ends in `auditctl -R /etc/audit/audit.rules`; auditctl names each
# rule it could not add as "There was an error in line N of FILE" (string verified in
# /usr/sbin/auditctl 1:4.1.2-1ubuntu0.1 on Lumen).

# audit_error_lines — stdin: augenrules/auditctl output. Prints each failing line no.
audit_error_lines() {
  grep -oE 'There was an error in line [0-9]+ of ' | grep -oE '[0-9]+' | sort -un
}

# audit_rule_at FILE N — line N of the generated rules file.
audit_rule_at() { sed -n "${2}p" "$1"; }

# audit_rule_sources RULESDIR RULE — rules.d files that carry RULE as a whole line.
# augenrules copies rule lines verbatim (it only strips a trailing CR), so an exact
# fixed-string whole-line match finds the origin.
audit_rule_sources() {
  local d="$1" rule="$2" f l
  for f in "$d"/*.rules; do
    [ -f "$f" ] || continue
    while IFS= read -r l || [ -n "$l" ]; do
      l="${l%$'\r'}"
      if [ "$l" = "$rule" ]; then echo "$f"; break; fi
    done <"$f"
  done
}

# audit_watch_path RULE — the filesystem path a rule depends on: `-w PATH`,
# `-F dir=PATH` or `-F path=PATH`. Empty for syscall/control rules.
audit_watch_path() {
  awk '{
    for (i = 1; i <= NF; i++) {
      if ($i == "-w" && i < NF) { print $(i + 1); exit }
      if ($i ~ /^-w./) { print substr($i, 3); exit }
      if ($i ~ /^(dir|path)=/) { sub(/^(dir|path)=/, "", $i); print $i; exit }
    }
  }' <<<"$1"
}

# audit_disable_rule FILE RULE TAG — comment RULE out of FILE in place, keeping the
# original text after the tag so it can be restored by deleting the prefix. Writes
# through `cat >` so owner, mode and inode of FILE are preserved. Returns 1 when RULE
# is not a whole line of FILE (nothing changed).
audit_disable_rule() {
  local f="$1" tmp
  tmp="$(mktemp)" || return 2
  R="$2" T="$3" awk '
    { l = $0; sub(/\r$/, "", l)
      if (l == ENVIRON["R"]) { print "# " ENVIRON["T"] ": " $0; hit = 1 } else print }
    END { exit hit ? 0 : 1 }' "$f" >"$tmp"
  local rc=$?
  if [ "$rc" -eq 0 ]; then cat "$tmp" >"$f"; fi
  rm -f "$tmp"
  return "$rc"
}

# --- bes grub-initrd-fallback.service -----------------------------------------
# The unit is two `grub-editenv /boot/grub/grubenv unset ...` calls. grub-editenv
# refuses a block whose signature is wrong ("invalid environment block"), which is
# the 2026-08-08 Thor failure of this same unit (agents/work-queue/reports/
# fleet_update_20260808.md:55-58; fixed by backup + `grub-editenv create`).

# grubenv_state FILE [READER...] — ok | absent | unreadable | bad-header | bad-size:<n>
# bad-header is the corruption grub-editenv rejects; bad-size alone is reported but
# not treated as corruption (grub only checks the signature). READER (e.g. `sudo -n`)
# prefixes every read so a caller can look as root. wq#5337: a file that exists but
# cannot be read is `unreadable`, never `bad-header` — the first bundle read as the
# unescalated ssh user and a permission failure looked like a bad signature, which
# would have backed up and recreated a healthy env.
grubenv_state() {
  local f="$1" h n
  shift
  [ -e "$f" ] || "$@" test -e "$f" 2>/dev/null || { echo absent; return; }
  h="$("$@" head -c 24 "$f" 2>/dev/null)" || { echo unreadable; return; }
  [ "$h" = "# GRUB Environment Block" ] || { echo bad-header; return; }
  n="$("$@" stat -c %s "$f" 2>/dev/null)" || { echo unreadable; return; }
  [ "$n" = 1024 ] || { echo "bad-size:$n"; return; }
  echo ok
}

# initrd_fallback_order — stdin: `systemctl cat grub-initrd-fallback.service`.
# fixed | stale | none. Ubuntu LP#2156423 (fixed in grub2 for 26.04.1): the unit's
# After= named grub-common.service, renamed to grub2-common.service, so the two units
# ran in parallel and both rewrote /boot/grub/grubenv at boot. The loser reads a
# half-written block ("invalid environment block") while the file on disk ends up
# valid. stale = After= names the old unit and not the new one.
initrd_fallback_order() {
  local a
  a="$(grep -v '^[[:space:]]*#' | grep -E '^[[:space:]]*After=' | tr ' =' '\n\n')"
  if grep -qx 'grub2-common.service' <<<"$a"; then echo fixed
  elif grep -qx 'grub-common.service' <<<"$a"; then echo stale
  else echo none; fi
}

# initrd_fallback_dropin — the drop-in that applies the LP#2156423 ordering fix
# locally (After= in a drop-in is additive; redundant but harmless once the fixed
# grub2 package is installed).
initrd_fallback_dropin() {
  echo "# wq#5337: Ubuntu LP#2156423 — order after the renamed grub2-common.service so"
  echo "# the two units stop racing on /boot/grub/grubenv at boot. Remove once"
  echo "# grub2-common is upgraded (the packaged unit then carries the same After=)."
  echo "[Unit]"
  echo "After=grub2-common.service"
}

# --- maksoor max-brightness.service -------------------------------------------
# wq#3637 (2026-10-08) wrote this unit for the laptop panel (brightness 7142/7142);
# lumen_state maksoor.hardware (2026-11-16) records a board swap to a desktop
# i5-12500 with no internal panel, so the backlight device the unit writes is gone.

# backlight_targets — stdin: `systemctl cat` text. Prints each backlight device dir
# the unit names, one per line (glob entries kept as written).
backlight_targets() {
  grep -v '^[[:space:]]*#' | grep -oE '/sys/class/backlight/[^/[:space:]"'"'"';]+' | sort -u
}

# backlight_dropin TARGET... — the drop-in that makes the unit skip (inactive, not
# failed) when its device is absent, and run unchanged when it is present. With no
# named target, any backlight device at all is the condition.
backlight_dropin() {
  local t
  echo "# wq#5247: maksoor's 2026-11-16 board swap (desktop i5-12500, no internal panel)"
  echo "# removed the backlight device this unit writes, so it failed every boot. Skip"
  echo "# cleanly when the device is absent; runs as before if a panel comes back."
  echo "[Unit]"
  if [ "$#" -eq 0 ]; then
    echo "ConditionPathExistsGlob=/sys/class/backlight/*"
    return
  fi
  for t in "$@"; do
    case "$t" in
      *'*'*|*'?'*|*'['*) echo "ConditionPathExistsGlob=$t" ;;
      *) echo "ConditionPathExists=$t" ;;
    esac
  done
}
