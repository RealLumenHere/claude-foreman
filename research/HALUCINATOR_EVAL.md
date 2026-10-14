# wq#3876 — HALucinator Eval: High-Level HAL Emulation for Firmware Rehosting

ticket: wq#3876 | date: 2026-10-14

## VERDICT: **CATALOG — PROMOTE TO PILOT, do not install fleet-wide yet**

HALucinator is real, actively maintained, and solves a different problem than Fuzzware (wq#3875,
sibling ticket) — the two compose rather than compete. The catalog stub landed by wq#3850
(`limpet/examples/halucinator.toml`, origin branch `feat/wq3850-limpet-add-11-new-re-tools-to-catalog-a1`,
not yet merged to main as of this ticket) under-sells it: that manifest flags "repo/license not
independently re-verified" because of a fleet-search outage. This ticket re-verifies live against
GitHub/PyPI (search was up today) and finds a materially different, better-supported project than
the stub's unpinned "version 0" implies.

## 1. What it is

HALucinator ("Firmware Re-hosting Through Abstraction Layer Emulation", Clements et al., USENIX
Security 2020 — `https://www.usenix.org/system/files/sec20summer_clements_prepub.pdf`) rehosts
embedded firmware by running the binary under QEMU (via Avatar2) and replacing calls into HAL
library functions (STM32 HAL, Zephyr, mbed, etc.) with Python-side handlers, instead of modeling
the MMIO/peripheral registers those HAL functions would otherwise touch. This is the
high-level complement to low-level MMIO modeling.

Two lineages exist, confirmed via GitHub API today:

- `github.com/embedded-sec/halucinator` — the original USENIX-2020 research repo. Its own README
  says it is superseded: "For a more recent version of HALucinator see
  `https://github.com/halucinator`." Its handler coverage (`src/halucinator/bp_handlers/`:
  `atmel_asf_v3`, `generic`, `mbed`, `stm32f4`) is narrower than the successor.
- `github.com/halucinator/halucinator` ("Main HALucinator branch", org maintained by Sandia
  NTESS contributors) — the live fork. `pushed_at: 2026-08-19`, 61 stars, PyPI package `halucinator`
  v1.9.0. This is the one to evaluate/adopt, not the archived original.

## 2. Supported HAL libraries (ticket's named targets: STM32 HAL, Nordic nRF SDK, ESP-IDF)

Live directory listing, `github.com/halucinator/halucinator`, `src/halucinator/bp_handlers/`
(checked 2026-10-14):

```
atmel_asf_v3  bp_handler.py  bpv5  debugger.py  generic  intercepts.py
libopencm3  mbed  stm32f4  test  vxworks  zephyr
```

- **STM32 HAL: yes**, directly — `stm32f4/` handler package plus `libopencm3/` (a popular
  alternative STM32 HAL), and `test/STM32/example` ships a working STM32F469I UART demo with a
  CubeMX-project-to-Makefile converter (`src/tools/stm_tools/build_scripts/CubeMX2Makefile.py` in
  the archived repo's README, carried forward in spirit).
- **Nordic nRF SDK: partial, indirect.** There is no `nordic/` or `nrf/` handler directory. Modern
  Nordic firmware built on the nRF Connect SDK is Zephyr-based, so it rides the `zephyr/` handler
  (`zephyr_fs.py`, `zephyr_uart.py`) — but that's Zephyr support, not a Nordic-specific binding, and
  it does not cover the classic (pre-Connect-SDK) nRF5 SDK, which isn't Zephyr-based at all.
- **ESP-IDF: no.** No `esp`/`espressif`/`esp_idf` handler directory exists in either repo. ESP-IDF
  is unsupported out of the box; adding it would mean writing new `BPHandler` subclasses from
  scratch, the same lift as any unsupported HAL.

So of the ticket's three named targets, one is well-supported (STM32), one is indirectly covered
if the firmware is Zephyr-based (Nordic), and one has no handler at all (ESP-IDF). "Supports major
HAL libraries" needs that caveat attached everywhere this tool gets cited.

Architecture support is broader than embedded-only: the live fork's README lists ARM Cortex-M,
full ARM (e.g. ARM926), AARCH64, MIPS, PPC, and PPC64 — i.e. it isn't Cortex-M-only, unlike what
"MCU emulation" framing (e.g. the `awesome-embedded-security` listing surfaced by `fleet-search`)
suggests.

## 3. Ghidra integration for HAL identification

Two companion projects exist in the `halucinator` GitHub org, both Sandia NTESS-authored:

- **`GHALdra`** (`github.com/halucinator/GHALdra`) — a Ghidra plugin wrapping Ghidra's own PCode
  emulator, so HALucinator-style intercept/handler config can run *inside* Ghidra instead of
  QEMU/Avatar2. Built partly on `TheRomanXpl0it/ghidra-emu-fun` for Ghidra-API argument/type
  extraction.
- **`PMatch`** (`github.com/halucinator/PMatch`) — the actual "HAL identification" piece the
  ticket is asking about. It's a Ghidra script that builds PCode-based function hashes/databases
  (not byte- or strict-CFG-based, so it survives relocation/ASLR/absolute-address differences) and
  matches them against known-library FID databases (ships prebuilt `.fidb` files, including an
  `arm-none-eabi-libmatch-mbed.fidb`). This is what actually locates "this address is HAL function
  X" in a stripped binary — the step HALucinator's own handler-matching config otherwise requires
  a human to do by hand.

Caveat found by checking both repos' `pushed_at`, not assumed: **both are stale — last pushed
2021-10-11, 4 and 6 GitHub stars respectively.** Neither has had a commit in 5 years and neither is
referenced from the actively-maintained `halucinator/halucinator` README as a current dependency.
Treat "Ghidra integration" as a research-grade, unmaintained companion, not a supported feature of
the live project.

## 4. Python API

Core abstraction, read directly from
`src/halucinator/bp_handlers/bp_handler.py` in the live fork: a `BPHandler` base class plus a
`@bp_handler` decorator. A handler subclass registers Python methods against HAL function names
(e.g. `@bp_handler(['HAL_UART_Transmit'])`); at runtime HALucinator sets a QEMU breakpoint at that
symbol's address and, on hit, calls the Python method instead of letting the real HAL code execute
— the method returns `(intercept: bool, return_value: Any)` to either substitute a result or let
the original function run. Config (which functions map to which handler, memory layout, symbol
addresses) is split across three YAML files passed as repeated `-c=` args to the `halucinator` CLI
entrypoint.

Notable and unexpected (not in the ticket's framing, found while reading the live tree): the
project now ships `src/halucinator/mcp/` — a FastMCP-based MCP server (`server.py`, `manager.py`,
`_worker.py`, `session.py`), copyright-dated 2026, installed via `pip install -e '.[mcp]'`. It runs
one emulation session per subprocess worker (process-per-session, so a crashed/wedged firmware
emulator only takes down its own worker) and exposes register/memory read-write, breakpoints,
watchpoints, IRQ injection, symbol lookup, disassembly, and step/continue as MCP tools, supporting
several concurrent firmware images. This means HALucinator could be wired into Commander the same
way REA is (`TOOL_REGISTRY.md` §"REA") rather than only driven by the CLI — worth flagging for
whoever runs the eventual go/no-go, since it changes the adoption shape from "a CLI tool fworker
shells out to" to "a registrable MCP server."

## 5. Complement to Fuzzware (wq#3875)

Confirmed by design, not just by the ticket's framing: Fuzzware auto-models MMIO register
behavior to fuzz firmware *without* needing to know what HAL/library the firmware uses — it works
at the hardware-register level and is blind to API semantics. HALucinator instead needs to know
exactly which HAL the firmware is built on (hence the handler-per-library structure in §2) and
replaces whole API calls with host logic, so it only works where a matching (or hand-written)
handler exists, but the code that runs behaves at the semantic level (buffer in, bytes out) rather
than guessing register timings. A firmware blob with a supported HAL (STM32, Zephyr) and known
symbol layout is a better candidate for HALucinator; firmware with unknown/undocumented
peripherals or no handler available is Fuzzware's territory. They are not interchangeable; a real
rehosting pipeline would try HALucinator first where a handler exists and fall back to Fuzzware's
MMIO modeling where it doesn't.

## 6. Recommendation

1. **Pilot, not fleet install.** Scope is narrower than the catalog stub's category tag
   (`"category": "emulation"`, `limpet/tool-catalog.json` per wq#3850) suggests — it's a good fit
   only for the HAL libraries it actually has handlers for (STM32, Zephyr/mbed/libopencm3/Atmel
   ASF/VxWorks), not a general "firmware emulator."
2. **Use `halucinator/halucinator` (PyPI `halucinator` v1.9.0), never `embedded-sec/halucinator`**
   — the latter's own README says it's superseded.
3. **Do not route adoption through GHALdra/PMatch** for the Ghidra-identification step; both are
   5-year-stale single-digit-star repos. If HAL-function identification in a stripped binary is
   needed before HALucinator can be configured, Ghidra's built-in Function ID (FID) matching
   (already available via REA/Ghidra on Maksoor per `TOOL_REGISTRY.md`) covers the same idea
   without depending on an abandoned plugin.
4. **Flag the MCP server (§4) to whoever runs the go/no-go** — it's a stronger adoption case than
   a bare CLI install, following the same MCP-registration precedent as REA
   (`TOOL_REGISTRY.md` §"REA (Reverse Engineer Anything)"), but it also widens the blast radius
   (registers emulation control across Commander) the same way REA's hold discussion worried about.

## Sources (fetched live, 2026-10-14)

- USENIX paper: `https://www.usenix.org/system/files/sec20summer_clements_prepub.pdf`
- `https://github.com/embedded-sec/halucinator` (README, `src/halucinator/bp_handlers/` listing)
- `https://github.com/halucinator/halucinator` (repo metadata, README, `src/halucinator/bp_handlers/`
  and `src/halucinator/mcp/` listings and `mcp/README.md`, `src/halucinator/bp_handlers/bp_handler.py`)
- `https://github.com/halucinator/GHALdra` (README, repo metadata)
- `https://github.com/halucinator/PMatch` (README, repo metadata)
- `https://pypi.org/pypi/halucinator/json` (version 1.9.0)
- `limpet/tool-catalog.json` and `limpet/examples/halucinator.toml`, branch
  `feat/wq3850-limpet-add-11-new-re-tools-to-catalog-a1` (not yet merged — read via
  `git -C /home/lumen/foreman show origin/feat/wq3850-...:<path>`, since this worktree's branch
  predates that merge)
