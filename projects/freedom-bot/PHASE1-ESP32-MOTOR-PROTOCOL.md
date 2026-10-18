# wq#4266 Phase 1 — Freedom ESP32 board + motor protocol RE

Prior-art search (doctrine: search before winging RE), not a teardown — this
session is headless with no physical access to the chassis. Findings below
should gate the physical teardown so Frank's hands-on time isn't spent
re-deriving what the community already published.

## What "Polaris Freedom" actually is

VISION.md's hardware stack assumes a battery-powered, WiFi-connected
cordless robotic cleaner. Confirmed: Polaris FREEDOM / FREEDOM Plus is
Zodiac/Jandy's cordless robotic line (lithium-ion battery, Easy-Charge
caddy, four cleaning modes). It is NOT the older suction-side "Freedom"
cleaner some Polaris dealer pages group with the 65/165 (those are corded,
no onboard compute). Confirms VISION.md's "48V battery system" and "tracked
drive" premise is the right product line.

## The motor protocol is already reverse-engineered — it's iAquaLink

The robot's WiFi control path is Zodiac's iAquaLink ecosystem (same app/
cloud relay used for pool automation, pumps, and other Polaris robots). Two
independent open-source clients already document the wire protocol:

- `github.com/tekkamanendless/iaqualink` (Go, MIT) — client library +
  protocol notes, explicitly "including Polaris robots"
- `github.com/galletn/iaqualink` — Home Assistant integration exposing
  full directional control, battery, cleaning mode, and error state

### Command format (from tekkamanendless/iaqualink's documented responses)

Responses are ASCII-hex-encoded (no spaces), shaped `00 <command>
<specific response>`. Movement commands send the direction as its ASCII
letter byte after a `0A1B` prefix:

- `0A1B46` — forward (`0x46` = ASCII `F`)
- `0A1B42` — backward (`0x42` = ASCII `B`)
- left/right follow the same pattern (`L`/`R` byte)
- `0A11` — list status

Status polling returns a packed state: byte values `01` stopped, `02`
running, `03` finished, `0B` remote control, `0D`/`0E` error (first/after
10 min) observed; error-code byte separately enumerates pump/drive-motor
faults; cleaning-mode byte is bit-packed (`&0x0f` selects floor/wall/
waterline + standard/high, `&0x10` flags canister-full).

### Implication for the phase plan

Phase 2 (VISION.md) assumed custom ESP32 firmware would be needed for
serial/WiFi motor control. That may be unnecessary for basic directional
driving: the stock ESP32 firmware already accepts forward/backward/left/
right over WiFi via the iAquaLink relay, with an existing MIT-licensed
client to drive it. Custom firmware is still likely needed for low-latency
local control (cloud relay round-trip is unsuitable for real-time obstacle
avoidance) and for anything beyond the four directions + lift the stock
protocol exposes, but Phase 1/2 scope should split into:

1. **Stock-protocol bring-up** (no disassembly, software-only): vendor or
   reimplement the command set above against a real Freedom unit's account
   pairing, confirm it drives the chassis motors, measure relay latency.
2. **Physical RE** (needs the spare chassis in hand — queued as a physical
   task, not a gate on this ticket per Tier-0 instinct 3): open the sealed
   motor housings, identify the ESP32-WROVER's UART/GPIO header for a local
   control path that bypasses the cloud relay, and confirm motor driver IC
   + gearbox ratio.

## Open physical tasks (queued, not blocking)

- Open spare Freedom chassis, photograph ESP32-WROVER board + motor driver
  ICs, identify any exposed UART/debug header.
- Pair a real Freedom unit with an iAquaLink account to validate the
  command bytes above against real hardware and capture full status byte
  tables (schedule/runtime fields are only partially documented upstream).

## Sources

- github.com/tekkamanendless/iaqualink (Go client + protocol notes, MIT)
- github.com/galletn/iaqualink (Home Assistant integration)
- polarispool.com product page, Polaris FREEDOM Plus manual (notice-facile.com mirror) — confirms iAquaLink app pairing, battery spec
