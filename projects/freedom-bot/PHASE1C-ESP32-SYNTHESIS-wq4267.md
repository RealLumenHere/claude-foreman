# wq#4267 — Freedom ESP32-WROVER: protocol, flash method, Jetson UART, driver IC

This ticket's four questions (protocol / flash method / UART pins for a Jetson /
motor driver IC) are the same four questions three sibling tickets dispatched in the
same fan-out already answered: **wq#4266** (WiFi/app protocol), **wq#4271**
(community-RE survey — confirms no public teardown exists), and **wq#4272** (UART/PWM/
driver-IC/JTAG desk research). Re-running those searches here would reproduce their
`fleet-search` output verbatim (same DeepSeek-Search fallback session window,
2026-10-18 20:58-21:06) rather than add anything, so this doc synthesizes their
findings into the shape this ticket asked for, adds the one open question none of them
addressed (how to wire a Jetson to this board instead of a PC), and leaves a dedup
note for the dispatcher. Sources: `projects/freedom-bot/PHASE1-ESP32-MOTOR-PROTOCOL.md`,
`projects/freedom-bot/research/community-re.md`, `research/wq4272_freedom_esp32_motor_control.md`.

## 1) Motor control protocol

Two separate, non-conflicting links exist:
- **WiFi/app side (confirmed)**: the iAquaLink cloud relay. `0A1B<dir-ASCII>` drives
  forward/back/left/right (`0x46`/`0x42`/.../`...`), `0A11` polls status; two
  independently-written MIT clients (`tekkamanendless/iaqualink`,
  `galletn/iaqualink`) already decode this (PHASE1-ESP32-MOTOR-PROTOCOL.md §"motor
  protocol is already reverse-engineered").
- **Remote-to-robot underwater side (not decoded)**: Freedom Plus's handheld remote
  uses **LiFi** (modulated light through the water, not RF) — marketing-confirmed,
  never publicly decoded (community-re.md §"LiFi remote control").
- **ESP32-to-motor-driver side (unconfirmed prediction)**: likely direct GPIO PWM
  (LEDC) into a dual H-bridge, not a UART/SPI/I2C link to the driver chip — small
  single-MCU robot designs almost never put a smart serial-protocol controller
  between the MCU and a dumb H-bridge (wq4272_freedom_esp32_motor_control.md §1-2).

## 2) Flashing custom firmware via USB/JTAG — confirmed, generic to any ESP32-WROVER

- **USB/serial (esptool.py)**: UART0 = GPIO1 (TX0) / GPIO3 (RX0). Pull `GPIO0` low
  while toggling `EN` to enter download mode, then `esptool.py write_flash`.
- **JTAG**: GPIO12=MTDI, GPIO13=MTCK, GPIO14=MTMS, GPIO15=MTDO (OpenOCD+GDB) — can
  read flash even if the firmware has disabled UART.
- Neither interface is guaranteed to be broken out on Freedom's PCB as a header;
  that's a physical-task item, already queued by wq#4272 (not re-queued here).

## 3) UART pins for a direct Jetson connection — two viable paths, pick by goal

Neither sibling ticket was asked this framing, so this is new synthesis, not a
re-finding:
- **Path A — replace the firmware**: flash custom ESP32 firmware (via §2's UART0
  pins) that exposes a simple serial command set, then wire that same UART0
  (GPIO1/GPIO3, cross TX↔RX) directly to a Jetson's UART pins. Both sides are
  3.3V logic (Jetson Xavier NX UART is 3.3V-tolerant), so no level shifting is
  needed — only ground reference and TX/RX crossover. This gives the Jetson a
  local, low-latency link, which is the stated reason PHASE1 gives for still
  wanting custom firmware (cloud relay round-trip is unfit for real-time control).
- **Path B — keep the stock firmware**: skip UART entirely and have the Jetson
  speak the iAquaLink WiFi protocol from §1 directly (it already has the command
  bytes). No physical UART wiring needed, but every drive command pays whatever
  latency the stock firmware's WiFi/relay stack adds, and only the four stock
  directions are available.
- Which GPIOs the **stock** firmware uses for anything beyond UART0/JTAG (e.g. an
  internal app-processor link, if one exists) is unknown and can only be found by
  tracing the physical board — not assumed here.

## 4) Motor driver IC — unconfirmed, not re-guessed here

wq#4272 already narrowed this from the ticket's own candidate list (DRV8xxx / L298 /
TB6612) to "DRV8833 or TB6612 likely, L298 unlikely" by elimination (3.3V-logic fit,
package size, prevalence in comparable ESP32 small-robot builds) and marked it
explicitly unconfirmed pending reading the actual part-number silkscreen. Nothing
found in this session's searches changes that; repeating the same elimination
argument here would not make it more confirmed. Treat it as unresolved until the
physical-task bench pass (queued by wq#4266/wq#4272) reads the chip.

## Dedup note for the dispatcher

wq#4266, wq#4267 (this ticket), and wq#4272 were all claimed within the same
8-minute window (2026-10-18 20:58:39-21:06:49) asking overlapping or identical
"RE the Freedom ESP32 board" questions. wq#4271 covered the community-survey half
of the same ground too. Three of the four produced near-duplicate `fleet-search`
work before this ticket caught the overlap via `wq show`/`wq list` and merged
origin/main instead of re-running the same queries. Worth a note to whoever owns
the Freedom Bot fan-out: a quick `wq list | grep -i freedom` before dispatch would
have caught this overlap before four workers spent sessions on it.
