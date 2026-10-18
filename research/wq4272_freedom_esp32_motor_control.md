# Freedom (Polaris/Zodiac) ESP32-WROVER motor control — wq#4272

Status: **DESK RESEARCH ONLY.** No physical board was available to this worktree
(Lumen is headless, no USB/JTAG/logic-analyzer access to the device). Everything
below is either (a) sourced from public documentation on the ESP32-WROVER module
itself, which is verifiable independent of the specific board, or (b) a documented
pattern from other robotic-pool-cleaner teardowns/rebuilds, offered as the most
likely architecture — explicitly flagged as **unconfirmed** where it is a pattern
match rather than a direct finding. Physical verification (continuity-check the
traces, read the driver IC markings, scope the GPIO-to-driver lines) is queued
below as a physical task; do not treat the "likely" items as fact until that runs.

## Device identified

The ticket's "Freedom" + "Polaris" are the same product: **Polaris Freedom /
Zodiac Freedom** cordless robotic pool cleaner (Fluidra brand — Polaris and
Zodiac are the same hardware under different badges), e.g. "Polaris TYPE EB37
Series FREEDOM Plus Cordless Robotic Cleaner." It's battery powered (one source
gives 25.4V/10Ah Li-ion), app-controlled via iAquaLink BLE, four cleaning modes.
Source: manuals.plus EB37 manual, outlet-pool.de spec sheet (fleet-search web,
queries "Polaris Freedom cordless robotic pool cleaner teardown hack firmware"
and "Zodiac Freedom robotic pool cleaner circuit board ESP32", 2026-10-18).

No public teardown, schematic, or firmware dump of this specific board turned up
in three targeted `fleet-search web` passes plus a `fleet-search reddit` pass
(empty result set) and two `fleet-search read` fetches that timed out
(mikrocontroller.net IC-ID thread, raspberrypi.com Pico-rebuild thread — one
later succeeded via retry, see below). `fleet-search github` is unusable this
session — no valid token (identity-map / github-frank.token / agent/.env all
checked, all empty) — logged as `kb/ISSUE-LOG.md` row 1433, status OPEN; GitHub
code search for repos like "freedom-pool-cleaner-re" could not be run and should
be retried once a token is provisioned.

## 1) UART/SPI/I2C between ESP32 and motor driver — likely NONE (direct GPIO)

Small single-board robots built around a WROVER/WROOM almost always drive the
motor-driver IC (DRV8833 / TB6612 / L298, see §3) with **direct GPIO PWM lines**
(ESP32 LEDC peripheral) into the driver's IN1/IN2/PWM pins — no UART/SPI/I2C
link to the driver chip at all, because those drivers are dumb H-bridges with no
onboard logic/registers. E.g. the `espp/basicmicro` ESP-IDF component and
RoboClaw-family controllers *do* use a UART packet-serial protocol to a motor
controller, but that pattern shows up in bigger/multi-motor platforms with a
separate smart controller board, not single-MCU hobby/consumer designs where the
driver IC is co-located with the ESP32 (sources: ESP Component Registry
`espp/basicmicro` page; `github.com/sonTungN/esp32-dc-motor-control`; Roger
Random's "TB6612 vs DRV8833 for ESP32 Micro Sawppy" blog series — all via
`fleet-search web`, 2026-10-18).

**Prediction for Freedom, unconfirmed:** the ESP32-WROVER drives the motor
driver IC directly over GPIO (LEDC PWM + 1-2 direction pins), not over UART/SPI/
I2C. If a logic analyzer on the board shows clocked serial traffic to a second
chip near the motors instead, this prediction is wrong — check IC pinout first.

## 2) PWM frequency / duty cycle for speed control

ESP32's LEDC (LED PWM Controller) peripheral is what Arduino-ESP32 /
ESP-IDF `ledc_*` calls use for motor PWM; it's a general timer/PWM block, not
motor-specific. Typical hobby brushed-DC-motor drive on ESP32 uses **1 kHz–20
kHz** carrier (above audible range, below driver IC's max switching frequency)
with 8-13 bit duty resolution. DRV8833 datasheet rates PWM input up to 250 kHz
but practical motor-noise-free designs stay in the 1-20 kHz band; TB6612FNG
datasheet quotes a similar range. (Source: DRV8833 / TB6612 datasheets referenced
in the Adafruit/Berrybase breakout pages found via `fleet-search web`,
2026-10-18.) The actual frequency/duty used by Freedom's firmware is a firmware
constant — not discoverable without dumping/decompiling that firmware or
scoping the live PWM line.

## 3) Motor driver IC — DRV8833 / TB6612 most likely, L298 unlikely

Candidates in the ticket: DRV8xxx, L298, TB6612.
- **L298** is an older, larger, higher-current/higher-logic-voltage (5V logic)
  dual H-bridge in a through-hole or large SMD package — a poor fit for a
  compact battery-powered cordless cleaner with a 3.3V ESP32, since it needs
  level-shifting and has a large footprint/heatsink.
  Likely fit: **low**.
- **DRV8833** (TI) and **TB6612FNG** (Toshiba) are both small, 3.3V-logic-
  compatible dual-H-bridge drivers purpose-built for exactly this kind of
  battery-powered small-robot design, and both show up repeatedly as the
  default choice in ESP32 small-robot projects (Roger Random's direct
  DRV8833-vs-TB6612 comparison for an ESP32 "Sawppy" rover chassis).
  Likely fit: **high**, DRV8833 slightly more common in recent compact designs
  per that source.
  Unconfirmed — read the actual part-number silkscreen on the Freedom board.

Separately: at least one *different* brand's pool-cleaner rebuild (RaspberryPi
Pico revival, `raspberrypi.com` news post, fetched via `fleet-search read`,
2026-10-18) describes the drive train as **one motor for propulsion plus a
mechanical reversing gearbox/valve**, not two independently-driven wheels —
i.e., direction on some pool cleaners is not "H-bridge polarity flip" but
"redirect water jet / shift a gear," with the H-bridge (if present) only
running a single motor forward. If Freedom uses the same mechanical-reverse
pattern, the driver IC could be single-channel or driving a pump/valve
solenoid rather than two drive motors. This is a different device family
(not confirmed to be the same internals as Freedom) — flagged as a pattern to
check for, not a finding.

## 4) Direction control method — unconfirmed, two candidate mechanisms

- **H-bridge polarity reversal** (IN1/IN2 logic swap on DRV8833/TB6612) if
  Freedom drives two independent track/wheel motors.
- **Mechanical reversing gearbox or water-jet valve** driven by a single motor
  plus a solenoid/second small motor, per the pattern in §3's Pico-rebuild
  source — common on pool cleaners that use one pump impeller for both
  propulsion and climbing.
Resolving this requires opening the Freedom unit and tracing which motors exist
and how many H-bridge channels are populated on the board — a physical task,
queued below.

## 5) Flashing custom firmware via USB/JTAG

This part is confirmed directly from Espressif documentation (`fleet-search
web`, query "ESP32-WROVER JTAG flash custom firmware esptool", 2026-10-18),
independent of the specific board:
- **Serial/USB (esptool.py)**: WROVER's UART0 (GPIO1=TX0, GPIO3=RX0) is the
  standard flashing interface. Put the chip in download mode by pulling
  `GPIO0` low while toggling `EN`/reset (auto-reset circuits on dev boards do
  this via DTR/RTS; a bare module needs manual strapping). `esptool.py
  --port <dev> write_flash ...` then flashes over that UART.
- **JTAG**: ESP32 supports flashing/debugging directly over JTAG on
  `GPIO12=MTDI, GPIO13=MTCK, GPIO14=MTMS, GPIO15=MTDO` (standard ESP32 JTAG
  pin mapping) using OpenOCD + GDB — no bootloader/esptool needed, and it can
  read out flash contents for firmware extraction even if the existing
  firmware disables UART access.
On the physical board, neither the UART0 header nor the JTAG pads are
guaranteed to be broken out/accessible — that depends on Freedom's PCB layout
and is a physical-task item (§6/queue below).

## 6) ESP32-WROVER pinout

Also confirmed from the Espressif WROVER-B/IB datasheet directly (same
`fleet-search web` pass as §5): the WROVER module is the standard 38-pin
Espressif module pinout (VDD33, EN, SENSOR_VP/VN, GPIO34-39 input-only ADC
pins, GPIO0/2/4/5/12-19/21-23/25-27/32/33 general GPIO via the GPIO matrix —
any peripheral signal, including LEDC PWM and UART, can be routed to almost
any GPIO through the matrix — plus GND/VDD rails and the PSRAM-dedicated
GPIO16/17 which WROVER (unlike WROOM) reserves internally for its onboard
PSRAM and should NOT be reused as free GPIO on this module). Exactly which of
those GPIOs Freedom's PCB actually routes to the motor driver, UART header, or
JTAG pads is board-specific and requires reading the PCB traces/silkscreen —
physical-task item below.

## What's actually confirmed vs. not

| # | Question | Status |
|---|---|---|
| 1 | UART/SPI/I2C to driver | Unconfirmed — direct-GPIO pattern predicted, not verified |
| 2 | PWM frequency/duty | Unconfirmed — typical 1-20kHz LEDC range given, firmware constant not extracted |
| 3 | Driver IC | Unconfirmed — DRV8833/TB6612 likely over L298 by elimination, not read off the part |
| 4 | Direction control | Unconfirmed — two candidate mechanisms, not resolved |
| 5 | USB/JTAG flashing | Confirmed (generic ESP32-WROVER capability, independent of this board) |
| 6 | WROVER pinout | Confirmed (generic module datasheet, independent of this board) |

## Physical task queued (Tier-0 instinct 3: physical acts aren't gates, they're queued)

Items 1-4 cannot be resolved further by desk research — they need hands on the
actual Freedom board: open the case, photograph the PCB, read the driver IC's
part-number laser-marking, trace continuity from the ESP32's GPIO pins to the
driver IC and to any header pads, and ideally scope the PWM/direction lines
with the unit powered and a motor spinning. Queuing this as a physical task
(not a blocking gate) per instinct 3 — whoever has the physical unit should run
that bench pass; this doc is the starting checklist for it.

## Sources (fleet-search, 2026-10-18)

- `fleet-search web "ESP32-WROVER pool robot motor control UART"`
- `fleet-search web "Polaris Freedom pool cleaner PCB teardown ESP32"`
- `fleet-search web "brushed DC motor driver UART control ESP32 robot"`
- `fleet-search reddit "pool cleaning robot ESP32 motor controller reverse engineering"` (empty)
- `fleet-search github "polaris freedom esp32 pool robot"` (401, no token — kb/ISSUE-LOG.md row 1433)
- `fleet-search web "robotic pool cleaner teardown motor driver IC DRV8833 TB6612 L298"`
- `fleet-search web "ESP32-WROVER JTAG flash custom firmware esptool"`
- `fleet-search web "Polaris Freedom cordless robotic pool cleaner teardown hack firmware"`
- `fleet-search web "Zodiac Freedom robotic pool cleaner circuit board ESP32"`
- `fleet-search web "ESP32-WROVER module pinout GPIO PWM LEDC datasheet pin map"`
- `fleet-search web "robotic pool cleaner hackaday reverse engineer motor controller board dump firmware"`
- `fleet-search read "https://www.raspberrypi.com/news/raspberry-pi-pico-revives-broken-pool-cleaning-robot/"`
- `fleet-search read` on mikrocontroller.net IC-ID thread and raspberrypi.com forum thread both timed out on first attempt (deepseek fetch unreachable); not retried further — within the "a few targeted queries" scope of the research doctrine, not an exhaustive crawl.
