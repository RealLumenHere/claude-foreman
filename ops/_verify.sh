#!/usr/bin/env bash
# ops/_verify.sh — wq#1696 deploy-verify contract helpers for ops bundles.
#
# CONTRACT: every ops/wqNNNN-*/install.sh answers `install.sh --verify`:
#   * READ-ONLY. No writes, no sudo, no systemctl start/enable/reload. Safe to run
#     from a timer as the lumen user at any time.
#   * exit 0  only when every live target matches what this bundle ships.
#   * exit 1  when anything is missing or differs; one `MISS <what>` line each.
#   * exit 2  when the check itself could not run (unreadable target, bad args).
# The handler MUST be a literal `--verify)` case arm (or `[ "${1:-}" = --verify ]`)
# near the top of install.sh, before anything that installs: ops-deploy-drift only
# runs --verify on bundles where it finds that marker, because a script that does
# not know the flag would ignore it and INSTALL.
#
# Usage inside install.sh:
#   SRC="$(cd "$(dirname "$0")" && pwd)"
#   if [ "${1:-}" = --verify ]; then
#     . "$SRC/../_verify.sh"
#     vfile "$SRC/foo.py" /home/lumen/bin/foo
#     vunit_file "$SRC/foo.timer" /etc/systemd/system/foo.timer
#     vactive foo.timer
#     vdone
#   fi
_V_MISS=0
_V_ERR=0

_vmiss() { echo "MISS $*"; _V_MISS=$((_V_MISS + 1)); }

# vfile SRC DST — DST exists and is byte-identical to SRC.
vfile() {
  local src="$1" dst="$2"
  [ -r "$src" ] || { echo "ERR source unreadable: $src"; _V_ERR=1; return; }
  if [ ! -e "$dst" ]; then _vmiss "absent: $dst"; return; fi
  [ -r "$dst" ] || { echo "ERR target unreadable: $dst"; _V_ERR=1; return; }
  cmp -s "$src" "$dst" || _vmiss "differs: $dst (from $(basename "$src"))"
}

# vunit_file SRC DST — same as vfile; named separately so reports read as units.
vunit_file() { vfile "$@"; }

# vactive UNIT — unit is active (service) or active/waiting (timer). Read-only.
vactive() {
  local u="$1" st
  st="$(systemctl is-active "$u" 2>/dev/null || true)"
  case "$st" in active|activating) ;; *) _vmiss "not active: $u ($st)";; esac
}

# venabled UNIT — unit is enabled (timers that must survive reboot).
venabled() {
  local u="$1" st
  st="$(systemctl is-enabled "$u" 2>/dev/null || true)"
  case "$st" in enabled|static|alias) ;; *) _vmiss "not enabled: $u ($st)";; esac
}

# vcontains FILE REGEX — a live file carries a marker the bundle patched in
# (for install steps that edit a file rather than copy one).
vcontains() {
  local f="$1" re="$2"
  [ -r "$f" ] || { _vmiss "absent: $f"; return; }
  grep -qE -- "$re" "$f" || _vmiss "marker /$re/ missing in $f"
}

# vdone — print the verdict and exit with the contract code.
vdone() {
  if [ "$_V_ERR" -ne 0 ]; then echo "VERIFY ERROR"; exit 2; fi
  if [ "$_V_MISS" -ne 0 ]; then echo "VERIFY FAIL ($_V_MISS)"; exit 1; fi
  echo "VERIFY OK"; exit 0
}
