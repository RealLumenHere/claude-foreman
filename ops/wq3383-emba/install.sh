#!/usr/bin/env bash
# wq#3383 EMBA (e-m-b-a/emba) install — automated firmware security analyzer
# (static+dynamic analysis, emulation, SBOM/VEX).
#
# This bundle ships ONLY what a non-root, no-container-engine session can do:
# clone upstream and confirm the `emba` entrypoint itself runs. The actual
# dependency install (`installer.sh -d`) is a hard root + docker-ce
# requirement baked into upstream, not a quirk of this box:
#   - installer.sh:255 `if ! [[ ${EUID} -eq 0 ]] && [[ ${LIST_DEP} -eq 0 ]]`
#     refuses every mode except the deprecated `-l` (list-deps, no install).
#   - installer/ID1_ubuntu_os.sh:67 `apt-get install docker-ce -y` is the
#     only supported dependency path (`-F` native full install is marked
#     "deprecated and not supported anymore" in the script's own -F help
#     text).
#   - Even `-l` isn't actually rootless: it still shells out to apt/dpkg to
#     check installed versions, and fails the same way (dpkg frontend lock,
#     `are you root?`) confirmed live during this build (kb/ISSUE-LOG.md #1165).
# A BUILD worker's sandbox denies `sudo *` and `docker *` (except ps/logs)
# outright (harness/worker-settings.json), so none of that can run here.
# This is the third independent confirmation of the same wall (wq3383-a2,
# -a3, -a5) — follow-up ticket wq#3404 is the root+container-engine step.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
WORKDIR=/home/lumen/bin/emba-dist
WRAPPER=/home/lumen/bin/emba
BIN="$WORKDIR/emba"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  [ -d "$WORKDIR/.git" ] || _vmiss "absent: $WORKDIR/.git (emba not cloned)"
  vfile "$SRC/emba-wrapper" "$WRAPPER"
  if [ -x "$BIN" ]; then
    if out="$("$WRAPPER" -h 2>&1)"; then
      case "$out" in
        *"Prints this help message"*) ;;
        *) echo "ERR $WRAPPER -h ran but did not print the expected usage text"; _V_ERR=1 ;;
      esac
    else
      echo "ERR $WRAPPER -h exited non-zero"
      _V_ERR=1
    fi
  else
    _vmiss "absent or not executable: $BIN"
  fi

  # wq#3404: the dependency install (installer.sh -d, pulls the
  # embeddedanalyzer/emba container image) needs real root + a container
  # engine, which no BUILD-worker sandbox can grant itself (kb/ISSUE-LOG.md
  # #1160/#1164/#1165). It is NOT part of the base --verify contract above —
  # that must keep passing on this sandbox's root-free, docker-free clone.
  # Opt in with --deps once installer.sh -d has actually run somewhere with
  # root + a container engine.
  #
  # wq#3407: this reports absence via _vmiss (exit 1 / MISS), not _V_ERR
  # (exit 2). Both arms of the test are conclusive negatives, which the
  # contract (ops/_verify.sh:8-9, :85-87) assigns to MISS: no docker at all
  # means installer.sh -d — whose only supported path installs docker-ce —
  # cannot have completed, and a clean `image inspect` miss is the image
  # genuinely not being pulled. ERR is reserved for "I could not look", and
  # harness/liveness.py's ops_verified() reads rc=1 as STILL_BLOCKED but
  # rc=2 as UNKNOWN, so an ERR here would turn a correctly-diagnosed blocked
  # dependency into an indeterminate probe failure.
  if [ "${2:-}" = --deps ]; then
    if command -v docker >/dev/null 2>&1 && docker image inspect embeddedanalyzer/emba >/dev/null 2>&1; then
      echo "DEPS OK: embeddedanalyzer/emba image present"
    else
      _vmiss "DEPS MISSING: embeddedanalyzer/emba image not found (run: sudo $WORKDIR/installer.sh -d)"
    fi
  fi
  vdone
fi

mkdir -p "$(dirname "$WORKDIR")"
if [ ! -d "$WORKDIR/.git" ]; then
  git clone --depth 1 https://github.com/e-m-b-a/emba.git "$WORKDIR"
fi

cp "$SRC/emba-wrapper" "$WRAPPER"
chmod 755 "$WRAPPER"

echo "== verifying the emba entrypoint runs (no deps needed for -h) =="
"$WRAPPER" -h >/dev/null && echo "emba -h OK (exit 0)"

echo "emba cloned at $WORKDIR, wrapper at $WRAPPER"
echo "NOT done here: dependency install. Run on a host with real root AND"
echo "a container engine:  sudo $WORKDIR/installer.sh -d"
echo "then re-run:   $SRC/install.sh --verify"
