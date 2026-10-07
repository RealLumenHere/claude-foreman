#!/usr/bin/env bash
# ops/_retool_apt_install.sh — wq#3488 no-autostart guard for re-tool apt installs.
#
# Class bug: a Debian postinst `enable`s and `start`s any .service/.timer/.socket
# the package ships, even when the ticket only wanted an on-demand CLI (wq#3380:
# `apt-get install bettercap` quietly enabled+started bettercap.service — root,
# api.rest on loopback 8081, Restart=always — and it ran 2026-10-02 to 2026-10-06
# before unit-drift's enable-symlink diff caught it by accident).
#
# retool_apt_install PKG... installs PKG via apt-get the same way the existing
# ops bundles already do (update -> -s simulate -> refuse Remv/upgrade -> real
# install), but wraps the real install in a policy-rc.d(8) that denies every
# `start` request dpkg's invoke-rc.d makes during unpack/configure, and then
# masks any unit the installed packages shipped — so a daemon the ticket never
# asked for can neither start now nor re-enable itself on the next boot.
#
# Usage inside install.sh (after the needrestart + MISSING + simulate guards
# already used by ops/wq3386-rizin-cutter/install.sh etc — this replaces only
# the final `apt-get install -y $MISSING` line):
#   . "$SRC/../_retool_apt_install.sh"
#   retool_apt_install $MISSING
set -u

_RETOOL_POLICY_RC_D=/usr/sbin/policy-rc.d

retool_apt_install() {
  local pkgs="$*"
  [ -n "$pkgs" ] || return 0
  [ "$(id -u)" = 0 ] || { echo "retool_apt_install: run as root" >&2; return 2; }

  local had_policy=0
  [ -e "$_RETOOL_POLICY_RC_D" ] && had_policy=1
  local backup
  backup="$(mktemp)"
  if [ "$had_policy" = 1 ]; then cp -p "$_RETOOL_POLICY_RC_D" "$backup"; fi

  # exit 101 is the documented invoke-rc.d convention for "policy denies this
  # action" (policy-rc.d(8)): dpkg's postinst treats it as a deliberate no, not
  # an error, so the install still reports success with the daemon left stopped.
  printf '#!/bin/sh\nexit 101\n' > "$_RETOOL_POLICY_RC_D"
  chmod 0755 "$_RETOOL_POLICY_RC_D"

  local rc=0
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs || rc=$?

  if [ "$had_policy" = 1 ]; then
    cp -p "$backup" "$_RETOOL_POLICY_RC_D"
  else
    rm -f "$_RETOOL_POLICY_RC_D"
  fi
  rm -f "$backup"

  [ "$rc" = 0 ] || return "$rc"

  # Belt and suspenders: policy-rc.d stops the start, but the unit can still be
  # left *enabled* (symlinked into multi-user.target.wants/), which is what
  # unit-drift actually caught in wq#3380 — the service itself never ran again
  # after a stray restart, but the enable symlink survived. Disable AND mask
  # every unit the packages just installed shipped, so neither a manual
  # `systemctl start` nor a future reboot can bring it back without a human
  # explicitly unmasking it first.
  local pkg unit
  for pkg in $pkgs; do
    dpkg -L "$pkg" 2>/dev/null | grep -E '\.(service|socket|timer)$' | while read -r unit; do
      unit="$(basename "$unit")"
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      systemctl mask "$unit" >/dev/null 2>&1 || true
      echo "retool_apt_install: masked $unit shipped by $pkg (wq#3488 no-autostart guard)" >&2
    done
  done
  return 0
}

# retool_verify_no_autostart PKG... — --verify helper: fails (_vmiss) if any
# unit any of PKG shipped is enabled. Requires _verify.sh already sourced.
retool_verify_no_autostart() {
  local pkg unit st
  for pkg in "$@"; do
    dpkg -L "$pkg" 2>/dev/null | grep -E '\.(service|socket|timer)$' | while read -r unit; do
      unit="$(basename "$unit")"
      st="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
      case "$st" in
        masked|disabled|"") ;;
        *) _vmiss "$unit shipped by $pkg is-enabled=$st (wants masked/disabled)" ;;
      esac
    done
  done
}
