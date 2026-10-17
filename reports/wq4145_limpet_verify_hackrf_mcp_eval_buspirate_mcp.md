# wq#4145: verify thehappydinoa/hackrf-mcp, evaluate buspirate-mcp

**Verdicts.**
- **hackrf-mcp: CONFIRMED real. Wrap as RX-only v1.** MIT, Python, 10 registered tools.
  It has an ungated TX path that a Limpet plugin must gate or drop.
- **buspirate-mcp (mplogas/buspirate-mcp): ADD-VETTED. Wrap it, but do not rely on its own
  safety tiers.** MIT, Python, 33 tools covering UART/SPI/I2C/1-Wire/logic analyzer on
  Bus Pirate 6. Its "approval-write" tier can be self-confirmed by the model. Three
  "allowed-write" tools can switch on the target PSU, and two "read-only" tools can send
  arbitrary bus writes, including SPI chip erase.

Both repos were fetched directly from the GitHub API and cloned at pinned commits into
`/tmp/fworker-wq4145/`. The upstream code was read but never executed. Every claim below
cites `path:line` at the pinned SHA, so it can be re-checked with
`git -C <clone> show <sha>:<path>`.

`docs/mcp-server-evaluations.md` (cited in the ticket, Sections 3 and 8) lives in
`/home/lumen/projects/limpet/`. That is outside this worker's allowed directories, and the
same was true for wq#4140 (ISSUE-LOG row 1397). This report is the source of truth. Fold
the "Doc deltas" section below into Sections 3 and 8 of that doc.

## 1. thehappydinoa/hackrf-mcp

| Field | Verified value | How |
|---|---|---|
| Repo | `github.com/thehappydinoa/hackrf-mcp`, not a fork, not archived | `api.github.com/repos/...` |
| Pinned SHA | `d9fa2ee6bf58acd28e50a1cad98a4458e0e2ef67` (main, 2026-06-27) | `git rev-parse HEAD` |
| Tags / releases | **none** | `/tags` → `[]` |
| PyPI | **not published** (`pypi.org/pypi/hackrf-mcp/json` → 404) | curl |
| License | MIT, "Copyright (c) 2026 Aidan Holland" | `LICENSE:1-3`, API `spdx_id` |
| Activity | created 2026-06-27, 9 commits, last code commit 2026-06-27, pushed_at 2026-10-02, 2 stars | API + `git log` |
| Language / deps | Python ≥3.10, `mcp`, `numpy>=2.2.6`; entry point `hackrf-mcp = hackrf_mcp.server:main` | `pyproject.toml:8,25-38` |
| Host dependency | wraps the `hackrf_*` CLI binaries (Debian `hackrf` package), found via `shutil.which` or `--hackrf-tools-dir` / `HACKRF_TOOLS_DIR` | `cli.py:31`, `server.py:852` |
| Exec safety | `subprocess.run([path, *args])` takes an argv list with no shell, so no shell injection | `cli.py:42-51` |
| Tests | 51 offline tests (17 server, 27 iq, 7 cli); CI workflow present | `grep -c "def test_"` |

**Tool surface (10 registered, `server.py`).** All are `@mcp.tool`. Annotations are
`READ_ONLY`, `CONFIG` (readOnly=False, destructive=False) and `DANGEROUS`, defined at
`server.py:41-43`.

| Tool | Upstream annotation | What it does | Proposed Limpet `tools_energize` |
|---|---|---|---|
| `hackrf_info` (`:47`) | READ_ONLY | device enumeration | false |
| `hackrf_sweep` (`:62`) | READ_ONLY | RX spectrum sweep | false |
| `hackrf_transfer` (`:160`) | **CONFIG** | `direction: "rx"\|"tx"`, with `txvga_gain_db`, `amp_enable`, `repeat_tx` | **true, and v1 must refuse `direction="tx"`** (see below) |
| `hackrf_clock` (`:253`) | CONFIG | CLKIN/CLKOUT config; `clkout_enable` drives a signal out of an SMA | true |
| `hackrf_operacake` (`:301`) | CONFIG | Opera Cake antenna-switch config | true |
| `hackrf_biast` (`:358`) | CONFIG | **applies DC 3.3-5 V to the antenna port** (docstring `:369-372`) | true (this energizes the port) |
| `hackrf_iq_to_wav` (`:409`) | CONFIG | offline file conversion, writes a file | false |
| `hackrf_iq_spectrogram` (`:505`) | CONFIG | offline PNG render, writes a file | false |
| `hackrf_iq_info` (`:570`) | READ_ONLY | offline IQ metadata | false |
| `hackrf_iq_detect_bursts` (`:632`) | READ_ONLY | offline burst detection | false |

Three firmware-touching tools are **commented out, not registered**: `hackrf_debug`,
`hackrf_cpldjtag` and `hackrf_spiflash` (`server.py:710-845`, `# @mcp.tool(annotations=DANGEROUS)`).
The server cannot reflash the HackRF in its current state.

**Findings that change the wrap:**
1. **TX is reachable through a non-destructive-annotated tool.** `hackrf_transfer` with
   `direction="tx"` radiates RF. With `repeat_tx` it keeps transmitting until the timeout.
   Upstream marks this `destructiveHint=False` and relies only on its docstring
   (`server.py:188-195`). This conflicts with the gnuradio-mcp plugin's RX-only v1 stance
   and with wq#4147, which treats SDR TX as its own risk class
   (`plugins/limpet-plugin-gnuradio-mcp/limpet-plugin.toml:12-20`). An energize gate alone
   is not enough. A v1 plugin must reject `direction="tx"` at the Limpet layer until
   wq#4147 decides TX policy, the same way gnuradio-mcp is RX-only by scope.
2. **No path confinement.** The `file_path` and output paths in `hackrf_transfer` and the
   `hackrf_iq_*` tools are not resolved against any allowed root (grep for
   `resolve|is_relative_to|ALLOWED` finds nothing). RX writes and offline renders can
   therefore write anywhere the plugin user can. Run the plugin as the Limpet plugin user
   with a scoped working dir.
3. **No release artifacts.** Pin the install to the commit SHA
   (`pip install git+https://github.com/thehappydinoa/hackrf-mcp@d9fa2ee6…`), not to a
   tag or PyPI name.
4. **Name overlap, no conflict.** gnuradio-mcp ships a flowgraph called `hackrf_mcp.grc`
   (`plugins/limpet-plugin-gnuradio-mcp/README.md:16`). It is unrelated to this package's
   `hackrf_mcp` Python module. Mention it in the README so nobody confuses the two.

**Relation to gnuradio-mcp.** hackrf-mcp is useful alongside it, not instead of it. It needs
no GNU Radio or flowgraph, sweeps with the native `hackrf_sweep`, and does offline IQ burst
detection and spectrograms. Both support HackRF One `1d50:6089`
(`limpet/hardware.d/09-sdr.toml`). The two plugins must not open the device at the same
time, and upstream's own instructions say one `hackrf_*` command at a time
(`server.py:25-26`).

## 2. buspirate-mcp: evaluation

**Prior-art search.** fleet-search web was down (every SearXNG engine suspended) and
fleet-search github returned 401. Both are known outages (ISSUE-LOG rows 1315/1379). I
searched the unauthenticated GitHub API instead:
- `q=buspirate+mcp` found 2 repos. The only MCP server is **`mplogas/buspirate-mcp`**.
- `q=buspirate in:name` (98 repos) has no other MCP or agent server.
- PyPI and npm `buspirate-mcp` both return 404.

So there is one candidate.

| Field | Verified value | How |
|---|---|---|
| Repo | `github.com/mplogas/buspirate-mcp`, not a fork, not archived | API |
| Pinned SHA | `36589d5d9fbcb3018f8568a88cdad04a7c434299` (main, 2026-06-24) | `git rev-parse HEAD` |
| Tags / PyPI | none / not published | API, PyPI 404 |
| License | MIT, "Copyright (c) 2026 Marc Plogas" | `LICENSE:1-3` |
| Activity | created 2026-03-24, 24 commits, latest 2026-06-24, 2 stars | API + `git log` |
| Hardware | **Bus Pirate 6** over the BPIO2 FlatBuffers binary interface. Tests and docs target BP6 only (`README.md:111,121,140`) | README |
| Vendored protocol | git submodule `vendor/bpio2` → `DangerousPrototypes/BusPirate-BPIO2-flatbuffer-interface` @ `36a5302` (official upstream, has its own LICENSE) | `.gitmodules` |
| Deps | Python ≥3.11, `mcp`, `pyserial`, `cobs`, **`esptool>=4.0`**; run with `python -m buspirate_mcp` (`__main__.py`, no console script) | `pyproject.toml:12-20` |
| Tests | 220 tests (`test_tools` 95, `test_hardware` 42, `test_session` 38, `test_la_parsers` 22, `test_safety` 10, …); hardware tests marked `-m hardware` | `grep -c "def test_"`, `README.md:140` |

**Tool surface (33, `server.py:66-688`).**
- UART (7): `list_devices`, `verify_connection`, `scan_baud`, `open_uart`, `read_output`,
  `send_command`, `close_uart`.
- PSU (2): `set_voltage`, `set_power`.
- ESP flash (2): `enter_download_mode`, `read_flash` (esptool through the BP6 UART bridge).
- SPI (7): `open_spi`, `spi_probe` (JEDEC), `spi_read`, `spi_dump`, `spi_write`
  (erase + program + verify), `spi_transfer`, `close_spi`.
- I2C (6): `open_i2c`, `i2c_scan`, `i2c_read`, `i2c_write`, `i2c_dump`, `close_i2c`.
- 1-Wire (4): `open_1wire`, `onewire_search`, `onewire_read`, `close_1wire`.
- Logic analyzer / FALA (5): `la_prepare`, `la_command`, `la_analyze`, `la_identify`,
  `la_cleanup`.

This fills the HAL classes that earlier wrapped plugins leave empty: SPI-flash dump,
I2C/EEPROM, 1-Wire, and UART console. Debug-probe MCPs (openocd-, jlink-,
embedded-debugger-mcp) do not cover them. It covers part of the flashrom-mcp gap from
wq#4096 (Bus Pirate as the SPI programmer only, not flashrom's chip database or other
programmers), so the first-party flashrom-mcp build stays on the books.

**Upstream safety model and where it breaks.** `safety.py` defines three tiers:
read-only, allowed-write and approval-write. The dispatcher (`server.py:716-739`) blocks an
approval-write call unless `_confirmed` is the literal boolean `True`.

1. **The approval tier is not a human gate.** `_confirmed` is an ordinary tool argument
   (`server.py:184,201,363,472`). The model can re-call with `_confirmed=true` and nobody
   is asked. Treat it as a speed bump. The Limpet energize-confirm gate has to be the real
   gate.
2. **Three "allowed-write" tools energize the target.** `open_spi`, `open_i2c` and
   `open_1wire` take `voltage_mv`/`current_ma` (`server.py:286,410,533`). When
   `voltage_mv` is set, `hardware.configure_*` sets `psu_enable=True`
   (`hardware.py:220-223,259-262,295-298`). The PSU therefore turns on with no
   `_confirmed` step, which bypasses the tier that guards `set_power`/`set_voltage`.
3. **Two "read-only" tools can write to the target.**
   - `spi_transfer` sends any hex the caller supplies with no opcode filter
     (`tools.py:1244-1256`). `06` then `C7` (WREN + chip erase; the constants are at
     `tools.py:50,53`) wipes the flash through a "read-only" tool.
   - `la_command` sends a BP6 terminal command. `_validate_la_command`
     (`tools.py:1339-1357`) checks only for ASCII, a length of 512 or less, and balanced
     brackets, so bus writes pass.
4. **Other "allowed-write" tools change the target's state.**
   - `enter_download_mode` toggles the target's BOOT/EN GPIOs.
   - `send_command` types into the target's console.
   - `read_flash` leaves the BP6 in bridge mode, and the only exit is the physical button
     or a USB replug (`README.md:131`).
   - `read_flash` / `spi_dump` / `i2c_dump` write to a caller-supplied path or
     `engagement_path` with no confinement (commit `2770be7` "Revert read_flash
     output_path stripping").

**Proposed Limpet `[mcp.tools_energize]`** (gate anything that drives power, writes to the
target, or resets it; leave pure reads and session bookkeeping ungated):
- **true:** `set_voltage`, `set_power`, `spi_write`, `i2c_write`, `spi_transfer`,
  `la_command`, `enter_download_mode`, `read_flash`, `send_command`. Also gate `open_spi`,
  `open_i2c` and `open_1wire`, or ship a Limpet-side argument filter that refuses
  `voltage_mv` and leaves these three ungated.
- **false:** `list_devices`, `verify_connection`, `scan_baud`, `read_output`, `open_uart`,
  `close_*`, `spi_probe`, `spi_read`, `spi_dump`, `i2c_scan`, `i2c_read`, `i2c_dump`,
  `onewire_search`, `onewire_read`, `la_prepare`, `la_analyze`, `la_identify`,
  `la_cleanup`.

`open_uart` stays ungated because it only configures the BP's IO pins with no PSU argument
(`server.py:105-126`).

**Packaging notes for the plugin ticket:**
- Install with `git clone --recurse-submodules` pinned to `36589d5`. `pip install .` alone
  misses `vendor/bpio2`.
- `esptool` is a hard dependency even if ESP flash is never used.
- udev needs the BP6 CDC-ACM VID:PID. `limpet/hardware.d/03-multiprotocol-tools.toml`
  currently lists BP v3 and BP5 (`60-buspirate5.rules`). Add the BP6 entry from the
  device's real `lsusb`. Do not guess it.

## Doc deltas for `/home/lumen/projects/limpet/docs/mcp-server-evaluations.md`

- **Section 8 (SDR):**
  - hackrf-mcp moves from "unverified" to verified: MIT, 10 tools, SHA `d9fa2ee`, no
    releases.
  - Recommend an RX-only wrap, with `hackrf_transfer` `direction="tx"` refused until
    wq#4147 decides.
  - Gate `hackrf_biast`, `hackrf_clock` and `hackrf_operacake`.
- **Section 3 (flashrom / SPI gap):**
  - Add mplogas/buspirate-mcp as ADD-VETTED: MIT, 33 tools, BP6-only, SHA `36589d5`.
  - It partially covers the SPI/I2C gap. The first-party flashrom-mcp build stays.
  - Do not rely on its upstream approval tier. Use the gate map above.

## Follow-ups

Filed two plugin build tickets that consume the energize maps above:
- wq#4154: the hackrf-mcp plugin, RX-only. It depends on wq#4147 only if TX is ever
  re-enabled.
- wq#4155: the buspirate-mcp plugin.
