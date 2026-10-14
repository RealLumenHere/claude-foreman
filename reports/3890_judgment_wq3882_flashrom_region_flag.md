# wq#3890: adversarial verify + judgment on wq#3882 (flashrom MCP server)

- **Ticket verified:** wq#3882, merged `b52a58c9` on main,
  `feat(limpet): wq3882 build flashrom MCP server`
  (`ops/wq3882-limpet-flashrom-mcp-a1/`). `git diff --stat b52a58c9` on `flashrom_ops.py`,
  `server.py` and `tests/test_flashrom_mcp.py` in worktree `wq3890-a1` is empty, so the
  code tested below is the shipped code.
- **Verifier:** claude-sonnet-5 (same model as the author), worktree `wq3890-a1`
- **Escalation:** opus-5@high (contradiction found, so this is a judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED (narrow).** The `region=` parameter of `read_flash`
  cannot work against real flashrom for any chip, programmer, or region name. The rest
  of the bundle (five tools plus the non-region path of `read_flash`) works against the
  real binary. Confirm the bulk of wq#3882 and fix the one defect in a follow-up ticket.

## Scope

wq#3882 ships `flashrom_ops.py` (argv construction, subprocess calls), `server.py`
(FastMCP decoration only) and `tests/test_flashrom_mcp.py` (16 tests, all with
`subprocess.run` monkeypatched; see the module docstring at `tests/test_flashrom_mcp.py:1-10`).
No shipped test runs the real binary. `/usr/sbin/flashrom` is installed here
(`flashrom v1.6.0 on Linux 7.0.0-38-generic (x86_64)`), and its `dummy` programmer
emulates a real SPI chip without hardware. I re-ran every check myself by importing the
shipped module with a spy on `subprocess.run` that prints the argv the module actually
builds. I did not retype the argv by hand.

## What's right (re-verified against the real binary)

Command (excerpt; the full script imported `flashrom_ops` from the worktree with
`LIMPET_FLASHROM_BIN=flashrom` and wrapped `ops.subprocess.run` with an argv-printing spy):

```
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV']
detect_chip ok/rc/chips: True 0 [{'vendor': 'Winbond', 'chip': 'W25Q128.V', 'size_kb': 16384, 'bus': 'SPI'}]
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-r', '/tmp/fworker-wq3890/j_full.bin']
read full ok/rc: True 0 16777216
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-w', '/tmp/fworker-wq3890/j_full.bin']
write ok/rc: True 0 ['Updating flash chip contents... Erase/write done from 0 to ffffff']
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-v', '/tmp/fworker-wq3890/j_full.bin']
verify ok/rc: True 0 ['Verifying flash... VERIFIED.']
```

```
erase no-confirm refused: erase_chip refused: pass confirm=True to actually erase (no undo without a prior read_flash backup)
erase confirm ok/rc: True 0 ['Erase/write done from 0 to ffffff']
identify_programmer: []
```

- `detect_chip` (`flashrom_ops.py:79-82`): `CHIP_LINE_RE` (`:28-32`) parses real flashrom
  output correctly.
- Full-chip `read_flash` (`:85-95`, `region=""`), `write_flash` (`:98-107`) and
  `verify_flash` (`:110-117`) all return rc 0. The read produces a 16 MiB file, which
  matches the 16384 kB chip.
- `erase_chip` (`:120-129`): the `confirm=False` gate raises before any subprocess runs,
  and `confirm=True` really erases.
- `identify_programmer` (`:132-150`) runs the real `ops/wq3809-limpet-hal/hal.py detect`
  and parses its JSON. `[]` is correct because no USB programmer is attached.
- The shipped suite passes: `python3 -m pytest -q tests/test_flashrom_mcp.py` gives
  `16 passed in 0.24s`.

## Contradiction found

### The code

`flashrom_ops.py:90-92`:

```python
args = _programmer_args(programmer, chip) + ["-r", str(out)]
if region:
    args += ["-i", region]
```

No `-l <layout>`, `--ifd`, `--fmap` or `--fmap-file` is ever added, and neither
`flashrom_ops.read_flash` (`:85`) nor the MCP tool `server.py:44`
(`def read_flash(programmer, output_path, chip="", region="")`) has a parameter for
passing one. `server.py:45` and `README.md:14` (`` `-r` (`-i` for `region`) ``) both
advertise the region path as a working feature.

I confirmed the verifier's scope claim: `write_flash` (`:98`) and `verify_flash`
(`:110`) have no `region` parameter. `read_flash` is the only affected function.

### Reproduction (shipped module, real binary)

```
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-r', '/tmp/fworker-wq3890/j_region.bin', '-i', 'bios']
read region ok/rc: False 1
stderr: 'Region requested (with -i "bios"), but no layout data is available.\n'
stdout tail: ['flashrom v1.6.0 on Linux 7.0.0-38-generic (x86_64)', 'flashrom is free software, get the source code at https://flashrom.org']
region file exists: False
```

This matches the verifier's report exactly.

### It fails for every chip and region, before the chip is probed

Next I tried three emulated chips, including one pre-loaded with a real 16 MiB image,
and three region names covering the IFD, generic, and coreboot-fmap naming styles:

```
dummy:emulate=SST25VF032B bios ok= False rc= 1 probed= False stderr= 'Region requested (with -i "bios"), but no layout data is available.'
dummy:emulate=SST25VF032B fd ok= False rc= 1 probed= False stderr= 'Region requested (with -i "fd"), but no layout data is available.'
dummy:emulate=SST25VF032B COREBOOT ok= False rc= 1 probed= False stderr= 'Region requested (with -i "COREBOOT"), but no layout data is available.'
dummy:emulate=MX25L6436 bios ok= False rc= 1 probed= False stderr= 'Region requested (with -i "bios"), but no layout data is available.'
dummy:emulate=MX25L6436 fd ok= False rc= 1 probed= False stderr= 'Region requested (with -i "fd"), but no layout data is available.'
dummy:emulate=MX25L6436 COREBOOT ok= False rc= 1 probed= False stderr= 'Region requested (with -i "COREBOOT"), but no layout data is available.'
dummy:emulate=W25Q128FV,image=/tmp/fworker-wq3890/j_full.bin bios ok= False rc= 1 probed= False stderr= 'Region requested (with -i "bios"), but no layout data is available.'
dummy:emulate=W25Q128FV,image=/tmp/fworker-wq3890/j_full.bin fd ok= False rc= 1 probed= False stderr= 'Region requested (with -i "fd"), but no layout data is available.'
dummy:emulate=W25Q128FV,image=/tmp/fworker-wq3890/j_full.bin COREBOOT ok= False rc= 1 probed= False stderr= 'Region requested (with -i "COREBOOT"), but no layout data is available.'
```

`probed= False` means no `Found ... flash chip` line was printed. flashrom rejects the
argv while parsing the command line, before it initializes the programmer or reads the
chip. Chip type and flash contents (for example a real Intel descriptor on a real board)
therefore cannot change the outcome. The CH341A/TL866 programmers this server targets
would hit the same rejection, because it happens before any programmer code runs.

### Control: the only missing piece is a layout source

Raw flashrom, same argv plus a layout source:

```
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-r', '/tmp/fworker-wq3890/j_ctl.bin', '-l', '/tmp/fworker-wq3890/j.layout', '-i', 'bios']
 rc= 0 stdout_tail= ['Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on dummy.', 'Reading flash... done.'] stderr= ''
ARGV: ['flashrom', '-p', 'dummy:emulate=W25Q128FV', '-r', '/tmp/fworker-wq3890/j_ctl.bin', '--ifd', '-i', 'bios']
 rc= 1 stdout_tail= ['Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on dummy.', 'Reading ich descriptor... done.'] stderr= "Couldn't parse the descriptor!"
```

(`j.layout` = `00000000:00000fff fd` / `00001000:00ffffff bios`.) With `-l`, the region
read succeeds. With `--ifd`, flashrom gets past the command-line check, probes the chip,
and then fails for a legitimate reason: the emulated chip has no Intel descriptor. On a
real Intel board dump this is the path that would work. Adding a layout source fixes the
defect.

### The man page (read directly, not paraphrased)

`man -P cat flashrom` gives this SYNOPSIS:

```
               [-E|-x|-r [<file>]|-w [<file>]|-v [<file>]]
               [(-l <file>|--ifd|--fmap|--fmap-file <file>)
                 [-i <include>[:<file>]]]
```

In the synopsis, `-i` appears only inside the bracket that follows a layout source. It is
not an independent option. The `-i, --include` section says:

```
                 o Regions with a filename associated must not overlap. This is also considered am-
                   biguous.  Note:  This  is  checked  later  since it requires processing the lay-
                   out/fmap first.

              Examples:
                     To read regions named foo and bar in layout file  <layout>  into  region-sized
                     files foo.bin and bar.bin, run:

                        flashrom -p prog -r -l <layout> -i foo:foo.bin -i bar:bar.bin
                     ...
                        flashrom -p prog -w -l <layout> -i foo:foo.bin -i bar:bar.bin
                     ...
                        flashrom -p prog -v -l <layout> -i foo:foo.bin -i bar:bar.bin
```

All three examples use `-l <layout>`. The region names `bios`/`me`/`gbe`/`fd`/`pd`, which
an agent would naturally pass, are documented only under `--ifd` ("The on-chip descriptor
will be read and used to generate the layout"). `--fmap` / `--fmap-file` cover coreboot
regions such as `COREBOOT`. The man page never describes automatic layout detection.
Every layout source is an explicit opt-in flag, and the run above shows that v1.6.0
rejects `-i` without one. The verifier's reading of the man page is accurate.

### Why the shipped tests didn't catch it

`tests/test_flashrom_mcp.py:72-75` (`test_read_flash_with_region`) asserts that the argv
is exactly `[FLASHROM_BIN, "-p", "ch341a_spi", "-r", str(out), "-i", "bios"]`, against a
fake `subprocess.run` (`:35-47`) that always returns rc 0. The test therefore locks in the
broken argv as the expected one. This is not fabrication: the code exists and does what
its own test says. The test only checks what the author assumed flashrom accepts, never
what flashrom actually accepts. flashrom and its hardware-free `dummy` programmer were on
the box the whole time, so a single real-binary test would have caught this.

## Mitigating angles considered

- **Does flashrom find a layout on its own?** No. The rejection happens during
  command-line parsing, before the chip is probed (`probed= False` above), and the man
  page lists only explicit layout flags.
- **Is it dead code?** No. `region` is a documented parameter of a live MCP tool
  (`server.py:44-49`, `README.md:14`), and its docstring invites agents to use it. Reading
  just the BIOS or ME region of an Intel SPI dump is one of the most common things an RE
  agent would ask for. Callers who pass `region` will hit the bug every time.
- **Is the failure dangerous?** No, and this is the main reason I'm not blocking
  wq#3882. `read_flash` is read-only. The failure is loud (`ok: False`, `returncode: 1`,
  plus flashrom's own clear stderr, which `_run` passes through as designed at `:39-69`).
  No file is written, and nothing is erased or written to the chip. An agent sees right
  away that the call failed and can fall back to a full read. It does not produce a
  silent wrong result.

## Why this is narrow and doesn't flip the headline

Five of six tools, plus the default full-chip path of the sixth, work correctly against
the real binary. That covers the main workflow: detect, back up, write, verify, gated
erase, and programmer enumeration. The defect is one optional parameter on a read-only
tool, and it fails loudly and safely. It is still a real bug: the parameter is advertised
and can never work. The fix is small: add a layout source to `read_flash` and replace
the test that encodes the broken argv.

Verdict: **CONTRADICTION CONFIRMED (narrow).** `region=` is broken in every case. Confirm
the rest of wq#3882 as live and correct.

## Follow-up (for the dispatcher to file; not filed here)

Fix ticket against `ops/wq3882-limpet-flashrom-mcp-a1/`:

1. Give `read_flash` (`flashrom_ops.py:85-95`, `server.py:44-49`) a layout source, for
   example `layout_file: str = ""` (adds `-l`) and/or `layout_source` from
   `{"ifd", "fmap", "fmap-file"}`. Make `region` without a layout source raise
   `FlashromError` locally with a clear message, instead of letting flashrom reject it.
   Removing `region` entirely is an acceptable alternative. Update `README.md:14` to
   match.
2. Replace `tests/test_flashrom_mcp.py:72-75`, which currently encodes the broken argv.
3. Add at least one real-binary integration test using `-p dummy:emulate=W25Q128FV`
   (skipped when `shutil.which("flashrom")` is None) covering detect, full r/w/v, and a
   region read with `-l`, so this class of bug fails CI without hardware.
4. Consider giving `write_flash`/`verify_flash` the same layout+region support
   (region-scoped writes are the safer way to reflash only BIOS), but that is a new
   feature and not part of this fix.
