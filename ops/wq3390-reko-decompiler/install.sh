#!/usr/bin/env bash
# wq#3390 Reko decompiler install. Ghidra's processor spec set misses or only
# weakly supports several embedded/legacy cores -- i8051, H8, Infineon, CSky,
# CompactRisc, IA64, Blackfin, Etrax, Alpha, Cray, Kalimba, Angstrem -- that
# Reko (github.com/uxmal/reko) decodes natively; its CmdLine release ships a
# Reko.Arch.*.dll per core, confirmed by inspecting the release zip below.
# Installed user-local under /home/lumen/opt (no root, no apt): a .NET 8
# runtime via Microsoft's official dotnet-install.sh, plus the pinned,
# sha256-verified Reko CmdLine build, wired up behind a /home/lumen/bin/reko
# wrapper.
#
# Build-worker sandboxes (guard.sh) scope every write to the worker's own
# worktree + --add-dir roots, so this bundle can be staged here but not
# executed here -- the same split wq#858 (ops/wq858-trivy-weekly-scan)
# documents for root-only installs; this one needs no root, just a writable
# path outside the sandbox. Run it for real via `lumen-run` on Lumen itself,
# per ops/VERIFY_CONTRACT.md.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

REKO_VERSION="0.12.5"
REKO_COMMIT="418d3538ee"
REKO_ASSET="CmdLine-${REKO_VERSION}-x64-${REKO_COMMIT}.zip"
REKO_URL="https://github.com/uxmal/reko/releases/download/version-${REKO_VERSION}/${REKO_ASSET}"
REKO_SHA256="26a0705ed12928d4c0543e2f2c1846fe293220df66ddb41e922afa7c6a4ff9fe"

DOTNET_CHANNEL="8.0"
DOTNET_DIR="/home/lumen/opt/dotnet"
REKO_DIR="/home/lumen/opt/reko-${REKO_VERSION}"
REKO_LINK="/home/lumen/opt/reko"
WRAPPER="/home/lumen/bin/reko"

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/reko-wrapper.sh" "$WRAPPER"
  [ -x "$DOTNET_DIR/dotnet" ] || _vmiss "absent: $DOTNET_DIR/dotnet"
  [ -e "$REKO_LINK/decompile.dll" ] || _vmiss "absent: $REKO_LINK/decompile.dll"
  vflag "$WRAPPER" "reko decompile"
  vdone
fi

echo "== installing .NET ${DOTNET_CHANNEL} runtime (user-local, no root) =="
mkdir -p "$DOTNET_DIR"
if [ ! -x "$DOTNET_DIR/dotnet" ]; then
  DOTNET_INSTALL="$(mktemp)"
  trap 'rm -f "$DOTNET_INSTALL"' EXIT
  curl -sSL -o "$DOTNET_INSTALL" https://dot.net/v1/dotnet-install.sh
  bash "$DOTNET_INSTALL" --channel "$DOTNET_CHANNEL" --runtime dotnet --install-dir "$DOTNET_DIR" --no-path
fi

echo "== downloading Reko v${REKO_VERSION} (CmdLine, x64) =="
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
curl -sSL -o "${WORKDIR}/${REKO_ASSET}" "$REKO_URL"
echo "${REKO_SHA256}  ${WORKDIR}/${REKO_ASSET}" | sha256sum -c -

echo "== extracting =="
mkdir -p "$REKO_DIR"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
  "${WORKDIR}/${REKO_ASSET}" "$REKO_DIR"
ln -sfn "$REKO_DIR" "$REKO_LINK"

echo "== installing wrapper =="
install -m 755 "$SRC/reko-wrapper.sh" "$WRAPPER"

echo "== verifying =="
"$WRAPPER" --version
"$WRAPPER" --help | grep -q "reko decompile"

echo "== done: $WRAPPER -> $REKO_LINK (dotnet at $DOTNET_DIR) =="
echo "usage: reko decompile <firmware.bin> --arch <arch> --env <env>"
echo "rollback: rm -f $WRAPPER $REKO_LINK; rm -rf $REKO_DIR $DOTNET_DIR"
