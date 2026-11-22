# wq#5074 Miko 3 head-board UART identification (PCBite + 1.8V adapter) — bench runbook

Goal: find which of the GMR1/GMR2 pads on the head board (EMT6736A V1.0, MT8167) is Miko TX, which
is Miko RX, and at what baud, and capture one full boot log. Bench host: Maksoor
(FT232R `/dev/ttyUSB0`, qarnet serial-mcp, wq#4513; behind the wq#5139 guard as `serial-maksoor`).

Needs Frank at the bench for the probing and the power cycles. Everything else in this file is
ready to run.

## Parts

| Part | Role |
|---|---|
| Waveshare FT232 USB-TTL | host UART. **Set its VCCIO jumper to 3.3V, never 5V.** |
| 1.8V SPI-flash adapter (CH341A style) | level shifter. Bottom DIP-8 pins = 3.3V side, top socket = 1.8V side |
| PCBite probes | land on GMR1, GMR2, and a Miko GND pad/shield |

Adapter pin roles use SPI-flash SOIC-8 numbering: 1 CS, **2 MISO (DO)**, 3 WP, **4 GND**, **5 MOSI (DI)**,
6 CLK, 7 HOLD, **8 VCC**. On these adapters CS/MOSI/CLK/WP/HOLD are buffered 3.3V→1.8V and MISO
1.8V→3.3V (prior art: "Upgrading a motherboard's BIOS/UEFI (the hard way)", 2021, found via
`fleet-search web "1.8V SPI adapter CH341A schematic ..."`). That fixes the direction of each UART line:

| Waveshare | Adapter 3.3V side (bottom) | Adapter 1.8V side (top) | Miko |
|---|---|---|---|
| VCC (3.3V) | pin 8 (powers the adapter's 1.8V LDO) | pin 8: **leave unconnected** | — |
| GND | pin 4 | pin 4 | GND pad (PCBite) |
| TXD | pin 5 MOSI | pin 5 | Miko **RX** pad |
| RXD | pin 2 MISO | pin 2 | Miko **TX** pad |

Never tie the adapter's 1.8V side pin 8 to Miko: it would back-feed Miko's 1.8V rail from the LDO.

## Procedure (listen first, drive last)

The pad roles are unknown, so step 2 connects nothing that can drive a Miko pad. The host TX
path (pin 5) goes on only after the Miko TX pad has been identified, so the adapter can never
drive into a Miko output.

0. **Levels, Miko powered, nothing attached.** Multimeter DC, black on Miko GND: measure GMR1 and
   GMR2. Expect about 1.8V idle (UART idles high) or 0V. **Over 2.0V: stop.** The pad is not a
   1.8V domain and this adapter is the wrong tool. Write the readings into the ticket.
1. **Wire GND and the RX path only:** Waveshare↔adapter as in the table, adapter top pin 4 to Miko
   GND, adapter top pin 2 on a PCBite probe. Do **not** connect top pin 5 yet.
2. **Listen on GMR1:** probe top pin 2 onto GMR1, then capture across a power cycle (below). Repeat
   on GMR2. Analyze each capture. The pad whose verdict is `clean` is Miko TX.
   * `no_data` / `stuck_line` on one pad and `clean` on the other is the expected result.
   * `baud_mismatch` on a pad means it is a TX at another rate. Recapture at the next rate the
     analyzer prints (sweep order 921600, 115200, 460800, 1500000, 57600, 230400).
   * `noisy` means check GND first, then probe contact.
3. **Wire host TX:** top pin 5 onto the other pad (Miko RX). Send a single CR (serial-mcp `write`,
   energize-gated, needs an operator `confirm_token`) and look for a prompt or echo.
4. Record in the ticket: GMR1/GMR2 role, baud, stages seen, and capture path. Then run
   `lumen_state.py ingest miko3 head_uart "<pad roles, baud>" --valid-from <date> --source wq#5074`.

## Capture

**Route A, serial-mcp (read-only, no token).** From a Claude session that loads `serial-maksoor`:
`open port=/dev/ttyUSB0 baud_rate=921600`, power-cycle Miko, poll `read` / `get_log` for about 30s,
then save the bytes to a file. `capture_boot` would also work, but it is energize-gated (wq#5139),
so Route A avoids needing a token.

**Route B, direct on Maksoor (read-only).** Close any serial-mcp session on the port first. The
analyzer opens the port with DTR/RTS deasserted and never writes:

```
ssh lumen@100.117.66.102 '/home/lumen/bin/fleet-exec copy maksoor /home/lumen/foreman/ops/wq5074-miko-uart-bench/boot_log_analyze.py /tmp'
ssh lumen@100.117.66.102 '/home/lumen/bin/fleet-exec run maksoor -- python3 -c "import serial"'
ssh lumen@100.117.66.102 '/home/lumen/bin/fleet-exec run maksoor -- python3 /tmp/boot_log_analyze.py --capture /dev/ttyUSB0 --baud 921600 --seconds 30 --save /tmp/miko_gmr1_921600.bin'
```

Power-cycle Miko within the 30s window. Repeat with `_gmr2_` in the filename for the other pad.
If the `import serial` check fails, use Route A, or install pyserial on Maksoor
(`sudo apt install python3-serial`).

**Analyze any saved capture** (file mode needs no pyserial):

```
ssh lumen@100.117.66.102 '/home/lumen/bin/fleet-exec run maksoor -- python3 /tmp/boot_log_analyze.py /tmp/miko_gmr1_921600.bin --baud 921600'
```

Exit codes: 0 `clean`, 1 `no_data`/`stuck_line`, 2 `baud_mismatch`/`noisy`, 3 IO error.

## Notes

* FT232R at 921600: the divisor gives 923077 baud (+0.16%), well inside 8N1 tolerance.
* 921600 comes from the ticket. MT8167 boards in prior art (e.g. Monsieur Cuisine Connect,
  EliasKotlyar/Monsieur-Cuisine-Connect-Hack) do not pin the console rate in any snippet found.
  That is why the analyzer judges the baud rather than assuming it.
* Inter-board GD32 link is a different UART: 460800 8N1, command protocol, not a console
  (`lumen_state.py current miko3`). A clean-text verdict with no boot-stage markers on a pad may
  be that link. Read the head of the capture before calling it the console.
* Tests: `python3 -m pytest -q ops/wq5074-miko-uart-bench/tests`. Wrong-baud cases come from a
  bit-level 8N1 simulator, not hand-made garbage bytes.
