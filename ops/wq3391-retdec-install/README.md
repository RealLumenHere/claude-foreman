# wq#3391 — RetDec retargetable decompiler

Installs [RetDec](https://github.com/avast/retdec) (avast/retdec), a
retargetable machine-code decompiler based on LLVM, for ARM/MIPS/x86 reverse
engineering work.

No apt package exists for this tool on this distro, so `install.sh` fetches
the official prebuilt `v5.0` Linux release tarball from GitHub instead of
building LLVM + RetDec from source (~180MB release vs. a multi-hour source
build for the same bits).

## Layout

- extracted release -> `/home/lumen/bin/retdec-5.0/`
- PATH entry -> `/home/lumen/bin/retdec-decompiler` symlinked to
  `retdec-5.0/bin/retdec-decompiler`

## Usage

```
retdec-decompiler --version
retdec-decompiler -a x86 -e little -b 32 input.bin
retdec-decompiler -a arm  -e little -b 32 input.bin
retdec-decompiler -a mips -e big    -b 32 input.bin
```

`retdec-decompiler --help` lists the full `--arch` set: `mips`, `pic32`,
`arm`, `thumb`, `arm64`, `powerpc`, `x86`, `x86-64`.

## Verify / reinstall

```
bash install.sh --verify   # read-only; confirms the symlink + arch coverage
bash install.sh            # idempotent; skips the download if already current
```

## Rollback

```
rm /home/lumen/bin/retdec-decompiler
rm -r /home/lumen/bin/retdec-5.0
```
