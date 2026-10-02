# wq#3390 — install Reko decompiler

Reko (`github.com/uxmal/reko`, GPLv2) is a cross-platform binary decompiler.
Unlike Ghidra, its architecture plugins are small and numerous — the latest
CmdLine release (`version-0.12.5`, checked via
`https://api.github.com/repos/uxmal/reko/releases/latest` on 2026-10-02) ships
one `Reko.Arch.*.dll` per supported core, including several embedded/legacy
ones Ghidra's processor spec set handles poorly or not at all: i8051, H8,
Infineon, CompactRisc, CSky, IA64, Blackfin, Etrax, Alpha, Cray, Kalimba,
Angstrem (confirmed by listing the release zip's contents — 599 entries, full
list in the dispatch notes).

## What `install.sh` does

Installs everything user-local under `/home/lumen/opt` — no root, no `apt`:

1. `.NET 8` runtime via Microsoft's official `dotnet-install.sh`
   (`https://dot.net/v1/dotnet-install.sh --channel 8.0 --runtime dotnet`),
   into `/home/lumen/opt/dotnet`.
2. The `CmdLine-0.12.5-x64-418d3538ee.zip` asset (the only Linux-runnable
   build Reko publishes — the `.msi` and `WindowsDecompiler-*` assets are
   Windows-only), downloaded from the pinned release URL and checked against
   a sha256 recorded at dispatch time (upstream publishes no checksum file of
   its own, so this bundle pins one): `install.sh:23`.
3. Extracted with `python3 -m zipfile` (no `unzip`/`tar` dependency) to
   `/home/lumen/opt/reko-0.12.5`, symlinked at `/home/lumen/opt/reko`.
4. `reko-wrapper.sh` installed to `/home/lumen/bin/reko`, so `reko decompile
   <file> --arch <arch> --env <env>` runs the `.NET`-hosted CLI directly.

## Verify

`install.sh --verify` (per `../VERIFY_CONTRACT.md`) is read-only: checks the
wrapper is byte-identical to the shipped copy, the `.NET` runtime and
`decompile.dll` exist at their pinned paths, and that `reko --help` actually
advertises the `reko decompile` subcommand (`vflag`, not just presence — a
stale or broken install would fail `--help` and get caught here rather than
just existing on disk).

## Not done here

This build-worker sandbox cannot write outside its worktree + `--add-dir`
roots (`/home/lumen/opt` and `/home/lumen/bin`'s real binary, as opposed to
this repo's copy of the wrapper, are both out of reach), so — same split as
`../wq858-trivy-weekly-scan` documents for root-only installs — this bundle
is staged, not executed, in this session. It installs on Lumen for real via
`lumen-run` on Lumen itself, then `bash ops/wq3390-reko-decompiler/install.sh
--verify` confirms it landed.
