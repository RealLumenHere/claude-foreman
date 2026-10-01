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

# vflag BIN FLAG — the installed BIN's own `--help` advertises FLAG.
# For a bundle whose payload CALLS another binary with a flag: wq#3228 found
# ops/wq1747-litellm-key-rotation gating a rotation on
# `stale-env-audit --fail-on-drift`, against an installed build that implemented neither
# flag. It scanned argv by substring, so it ignored both and exited 0 on a whole-fleet
# audit — a gate that could not fail. A vfile on the caller cannot catch that; only asking
# the callee what it implements can. Read-only: `--help` neither writes nor needs sudo.
# A non-zero --help is a MISS, not an ERR: a build old enough to lack the flag is usually
# also old enough to lack --help, and that is an answer ("not deployed"), not a failure to
# look. Only an unrunnable binary is ERR.
vflag() {
  local bin="$1" flag="$2" out
  [ -x "$bin" ] || { _vmiss "absent or not executable: $bin"; return; }
  out="$("$bin" --help 2>&1)" || true
  if [ -z "$out" ]; then echo "ERR $bin --help produced nothing"; _V_ERR=1; return; fi
  case "$out" in *"$flag"*) ;; *) _vmiss "$bin --help does not advertise $flag";; esac
}

# --- other fleet nodes (wq#2985) --------------------------------------------
# A bundle that installs onto Anubis, Bes, Sia or the MacPro6,1 group has no
# local target at all, so vfile/vactive can only ever report it absent. wq#1732
# read exactly that and filed three live bundles as PENDING. These reach the
# node that actually runs the payload. Still read-only: every remote command is
# a query, and an unreachable node is ERR (exit 2), never a MISS — "I could not
# look" must not read as "it is not deployed".
_VSSH="ssh -o BatchMode=yes -o ConnectTimeout=8"

# vremote_up HOST — HOST answers at all. Call once before the per-file checks.
vremote_up() {
  local h="$1"
  $_VSSH "$h" true 2>/dev/null || { echo "ERR unreachable: $h"; _V_ERR=1; return 1; }
}

# vremote_file HOST SRC DST — DST on HOST is byte-identical to local SRC.
vremote_file() {
  local h="$1" src="$2" dst="$3" want got
  [ -r "$src" ] || { echo "ERR source unreadable: $src"; _V_ERR=1; return; }
  want="$(sha256sum "$src" | cut -d' ' -f1)"
  got="$($_VSSH "$h" "sha256sum '$dst' 2>/dev/null | cut -d' ' -f1" 2>/dev/null || true)"
  case "$got" in
    "$want") ;;
    "")      _vmiss "$h: absent $dst" ;;
    *)       _vmiss "$h: differs $dst (from $(basename "$src"))" ;;
  esac
}

# vremote_unit HOST UNIT — UNIT is enabled and active on HOST.
vremote_unit() {
  local h="$1" u="$2" st
  st="$($_VSSH "$h" "systemctl is-enabled '$u' 2>/dev/null" 2>/dev/null || true)"
  case "$st" in enabled|static|alias) ;; *) _vmiss "$h: $u not enabled (${st:-unknown})";; esac
  st="$($_VSSH "$h" "systemctl is-active '$u' 2>/dev/null" 2>/dev/null || true)"
  case "$st" in active|activating) ;; *) _vmiss "$h: $u not active (${st:-unknown})";; esac
}

# vremote_user_unit HOST UNIT — UNIT is enabled and active in HOST's *user* manager, for a
# bundle that installs a `systemctl --user` timer rather than a system one (wq#3332's hourly
# chat-digest-pull.timer is the first). A non-interactive ssh has no user session, so
# `systemctl --user` cannot find its bus unless XDG_RUNTIME_DIR is pointed at the login
# user's runtime dir; without it every user unit reads "Failed to connect to bus" and a
# naive check turns that into a MISS it invented. Bus failure is ERR, per wq#2985.
vremote_user_unit() {
  local h="$1" u="$2" uctl out
  uctl='XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user'
  out="$($_VSSH "$h" "$uctl is-enabled '$u' 2>&1" 2>/dev/null || true)"
  case "$out" in
    enabled|static|alias)       ;;
    *bus*|*Failed*|*refused*)   echo "ERR $h: user manager unreachable ($u: $out)"; _V_ERR=1; return ;;
    *)                          _vmiss "$h: $u not enabled (${out:-unknown})" ;;
  esac
  out="$($_VSSH "$h" "$uctl is-active '$u' 2>&1" 2>/dev/null || true)"
  case "$out" in active|activating|waiting) ;; *) _vmiss "$h: $u not active (${out:-unknown})";; esac
}

# vdone — print the verdict and exit with the contract code.
vdone() {
  if [ "$_V_ERR" -ne 0 ]; then echo "VERIFY ERROR"; exit 2; fi
  if [ "$_V_MISS" -ne 0 ]; then echo "VERIFY FAIL ($_V_MISS)"; exit 1; fi
  echo "VERIFY OK"; exit 0
}
